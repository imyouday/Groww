"""Guardrail tests: the acceptance tests for the safety claims (architecture.md §13, PRD C1-C7).

Each test here corresponds to a claim the deliverable makes out loud. V4 is the one that matters
most: a number that is not in the assembled context, digit for digit, must never reach a user, and
the extractive composer is the path that is allowed to say the answer instead.
"""

from __future__ import annotations

import logging

import pytest

from src import guardrails, pipeline
from src.generation import ExtractiveGenerator
from src.guardrails import (
    V1_sentinels,
    V2_length,
    V3_on_topic,
    V4_numeric_grounding,
    V5_banned_terms,
    V6_no_urls,
    build_answer,
    route,
    validate,
)
from src.models import (
    Answer,
    AssembledContext,
    ChunkRecord,
    DraftAnswer,
    FactFamily,
    GateResult,
    Intent,
    ScoredChunk,
    SectionType,
)
from src.registry import Registry, load_registry

SCHEME_NAME = "HDFC Large Cap Fund - Direct Growth"
PAGE_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"

FEE_TEXT = (
    "Min. for 1st investment\n₹100\nMin. for SIP\n₹100\n"
    "Expense ratio\n1.03%\nExit load of 1% if redeemed within 1 year"
)


def make_context(
    text: str = FEE_TEXT,
    terms: list[str] | None = None,
    url: str = PAGE_URL,
    source_id: str = "S1",
    scheme_id: str = "S1",
) -> AssembledContext:
    """Build an AssembledContext holding one chunk, the shape the validators consume."""
    chunk = ChunkRecord(
        chunk_id="c1",
        source_id=source_id,
        scheme_id=scheme_id,
        scheme_name=SCHEME_NAME,
        section="Minimum investments / Exit load",
        section_type=SectionType.FEES,
        text=text,
        embed_text=text,
        url=url,
        title="HDFC Large Cap Fund Direct Growth",
        fetched_at="2026-09-27",
        ordinal=0,
        token_count=120,
    )
    scored = ScoredChunk(
        chunk=chunk,
        dense=0.8,
        keyword_boost=0.2,
        final=0.86,
        mmr_selected=True,
        matched_terms=terms if terms is not None else ["expense ratio", "min. for sip"],
    )
    return AssembledContext(
        query="What is the expense ratio?",
        scheme_id=scheme_id,
        fact_family=FactFamily.EXPENSE_RATIO,
        chunks=[scored],
        total_tokens=120,
        top_score=0.86,
        context_text=f"[1] {SCHEME_NAME} | Minimum investments | source: {url}\n    {text}",
    )


def draft(text: str, generator: str = "llm", sentinels: list[str] | None = None) -> DraftAnswer:
    """Build a DraftAnswer carrying text, the generator that wrote it, and any sentinel."""
    return DraftAnswer(text=text, generator=generator, sentinels=sentinels or [])


class TestV1Sentinels:
    def test_refuse_sentinel_is_recognised(self) -> None:
        assert V1_sentinels(draft("", "llm", ["REFUSE"])) == "refusal"

    def test_not_in_corpus_sentinel_is_recognised(self) -> None:
        assert V1_sentinels(draft("", "extractive", ["NOT_IN_CORPUS"])) == "not_in_corpus"

    def test_a_normal_draft_has_no_sentinel(self) -> None:
        assert V1_sentinels(draft("The expense ratio is 1.03%.")) is None


class TestV2Length:
    def test_three_sentences_pass(self) -> None:
        assert V2_length("One thing. Two things. Three things.", 3) is True

    def test_four_sentences_are_rejected(self) -> None:
        assert V2_length("One. Two. Three. Four.", 3) is False

    def test_decimals_and_abbreviations_do_not_count_as_sentence_ends(self) -> None:
        text = "The expense ratio is 1.03% p.a. Exit load is 1% for 1 yr. Min. SIP is Rs. 100."
        assert V2_length(text, 3) is True


class TestV3OnTopic:
    def test_a_short_answer_is_rejected(self) -> None:
        assert V3_on_topic("It is 1.03%.", make_context()) is False

    def test_a_fact_using_corpus_vocabulary_passes(self) -> None:
        text = "The expense ratio of the scheme is 1.03% as published on the page."
        assert V3_on_topic(text, make_context()) is True

    def test_a_confident_unrelated_sentence_is_rejected(self) -> None:
        text = "The weather in Mumbai is pleasant this week and the trains are running."
        assert V3_on_topic(text, make_context()) is False

    def test_a_lifted_table_fact_passes_even_though_it_is_three_words(self) -> None:
        """The corpus holds "Expense ratio" and "1.03%" on two lines, so the answer is three words."""
        assert V3_on_topic("Expense ratio 1.03%", make_context()) is True

    def test_a_value_free_stub_is_still_rejected(self) -> None:
        assert V3_on_topic("Expense ratio", make_context()) is False


class TestV4NumericGrounding:
    def test_a_grounded_percentage_passes(self) -> None:
        ok, offending = V4_numeric_grounding("The expense ratio is 1.03%.", make_context())
        assert ok is True
        assert offending == []

    def test_an_invented_percentage_fails(self) -> None:
        ok, offending = V4_numeric_grounding("The expense ratio is 0.45%.", make_context())
        assert ok is False
        assert "0.45%" in offending

    def test_an_invented_lock_in_period_fails(self) -> None:
        context = make_context("Lock-in period 3 years from the date of investment.")
        assert V4_numeric_grounding("The lock-in period is 3 years.", context)[0] is True
        ok, offending = V4_numeric_grounding("The lock-in period is 5 years.", context)
        assert ok is False
        assert "5 years" in offending

    def test_a_grounded_rupee_amount_passes_and_an_invented_one_fails(self) -> None:
        assert V4_numeric_grounding("The minimum SIP is ₹100.", make_context())[0] is True
        assert V4_numeric_grounding("The minimum SIP is ₹500.", make_context())[0] is False

    def test_parsed_numbers_are_not_accepted(self) -> None:
        context = make_context("Expense ratio 1.5%")
        ok, offending = V4_numeric_grounding("The expense ratio is 1.50%.", context)
        assert ok is False
        assert offending == ["1.50%"]

    def test_a_draft_from_the_extractive_generator_passes(self) -> None:
        context = make_context(terms=["expense ratio", "exit load"])
        composed = ExtractiveGenerator().generate(context, "expense ratio?", Intent.FACTUAL_FACT)
        assert V4_numeric_grounding(composed.text, context)[0] is True


class TestV5BannedTerms:
    def test_advice_language_is_rejected(self) -> None:
        hits = V5_banned_terms("You should consider this fund for your portfolio.", ("should", "best"))
        assert hits == ["should"]

    def test_a_neutral_factual_sentence_passes(self) -> None:
        assert V5_banned_terms("The expense ratio is 1.03%.", ("should", "best", "returns")) == []

    def test_performance_words_are_rejected(self) -> None:
        assert V5_banned_terms("This fund has the best returns.", ("returns",)) == ["returns"]

    def test_matching_is_on_word_boundaries(self) -> None:
        assert V5_banned_terms("The scheme holds 50 stocks.", ("hold",)) == []
        assert V5_banned_terms("Hold this fund.", ("hold",)) == ["hold"]

    def test_a_banned_word_inside_a_verbatim_corpus_sentence_is_not_a_hit(self) -> None:
        """"NIFTY 100 Total Return Index" is the benchmark's name, not a performance claim."""
        context = make_context("Fund benchmark\nNIFTY 100 Total Return Index")
        assert V5_banned_terms("Fund benchmark NIFTY 100 Total Return Index", ("return",), context) == []

    def test_a_tax_rule_from_the_corpus_is_not_a_hit_either(self) -> None:
        context = make_context("If you redeem within one year, returns are taxed at 20%.")
        text = "If you redeem within one year, returns are taxed at 20%."
        assert V5_banned_terms(text, ("returns",), context) == []

    def test_invented_advice_about_the_same_number_is_still_a_hit(self) -> None:
        context = make_context("If you redeem within one year, returns are taxed at 20%.")
        text = "You should redeem within one year, because returns are taxed at 20%."
        assert V5_banned_terms(text, ("should", "returns"), context) == ["should", "returns"]

    def test_a_missing_context_keeps_the_check_strict(self) -> None:
        context = make_context("Fund benchmark\nNIFTY 100 Total Return Index")
        assert V5_banned_terms("Fund benchmark NIFTY 100 Total Return Index", ("return",)) == ["return"]


class TestV6NoUrls:
    def test_a_url_is_rejected(self) -> None:
        assert V6_no_urls("See https://groww.in/mutual-funds/x for details.") is False

    def test_a_www_reference_is_rejected(self) -> None:
        assert V6_no_urls("Check www.groww.in for the scheme page.") is False

    def test_an_external_attribution_is_rejected(self) -> None:
        assert V6_no_urls("According to a broker note, the fee is low.") is False

    def test_plain_factual_text_passes(self) -> None:
        assert V6_no_urls("The expense ratio is 1.03% and the minimum SIP is ₹100.") is True


class TestValidate:
    def test_a_clean_draft_reports_passed(self) -> None:
        verdict = validate(draft("The expense ratio is 1.03% for this scheme."), make_context())[1]
        assert verdict == "passed"

    def test_v1_wins_over_every_later_check(self) -> None:
        _, verdict = validate(draft("", "llm", ["REFUSE"]), make_context())
        assert verdict == "v1_refusal"

    def test_the_first_failing_validator_is_named(self) -> None:
        text = "One fact here. Two facts here. Three facts here. Four facts here. Five."
        _, verdict = validate(draft(text), make_context())
        assert verdict == "v2_length"

    def test_an_invented_number_is_named(self) -> None:
        _, verdict = validate(draft("The expense ratio is 0.45% for this scheme."), make_context())
        assert verdict == "v4_numeric"

    def test_the_report_records_the_offending_token(self) -> None:
        report = guardrails.validation_report(
            draft("The expense ratio is 0.45% for this scheme."), make_context()
        )
        assert report["v4_ungrounded"] == ["0.45%"]


class TestBuildAnswer:
    def test_the_citation_comes_from_the_top_chunk(self) -> None:
        result = build_answer(draft("The expense ratio is 1.03% for this scheme."), make_context())
        assert result.citation_url == PAGE_URL
        assert result.citation_source_id == "S1"

    def test_a_non_registry_url_yields_no_citation(self) -> None:
        context = make_context(url="https://evil.example/scheme")
        result = build_answer(draft("The expense ratio is 1.03% for this scheme."), context)
        assert result.citation_url is None

    def test_last_updated_is_the_source_fetch_date(self) -> None:
        result = build_answer(draft("The expense ratio is 1.03% for this scheme."), make_context())
        assert result.last_updated == "2026-09-27"

    def test_the_kind_is_factual_and_the_trace_names_the_generator(self) -> None:
        result = build_answer(
            draft("The expense ratio is 1.03% for this scheme.", "extractive"), make_context()
        )
        assert result.kind == "factual"
        assert result.trace["generator"] == "extractive"
        assert result.trace["guardrail"] == "passed"


class TestRoute:
    @pytest.mark.parametrize(
        ("intent", "kind", "link"),
        [
            (Intent.ADVICE_REQUEST, "refusal", "education"),
            (Intent.PERFORMANCE_REQUEST, "performance_redirect", "scheme_page"),
            (Intent.PII_REQUEST, "pii_refusal", "help"),
            (Intent.OUT_OF_CORPUS, "out_of_corpus", "education"),
            (Intent.SMALLTALK, "smalltalk", "none"),
            (Intent.FACTUAL_FACT, "not_in_corpus", "scheme_page"),
        ],
    )
    def test_each_kind_gets_its_template_and_one_registered_link(
        self, intent: Intent, kind: str, link: str
    ) -> None:
        reg = load_registry()
        context = make_context()
        result = route(intent, None, context, reg, None, "S1")
        assert result.kind == kind
        assert result.generator == "none"
        urls = {source.url for source in reg.sources}
        if link == "none":
            assert result.citation_url is None
        else:
            assert result.citation_url in urls
        assert "{link}" not in result.text

    def test_a_gate_failure_is_routed_to_not_in_corpus(self) -> None:
        gate = GateResult(False, 0.12, 0.35, "top score below tau", [])
        result = route(Intent.FACTUAL_FACT, gate, None, load_registry(), None, "S1")
        assert result.kind == "not_in_corpus"
        assert result.trace["gate_reason"] == "top score below tau"


class TestLoggingPolicy:
    def test_the_query_field_is_never_in_a_logged_trace(self) -> None:
        result = pipeline.answer("What is the expense ratio of the HDFC Large Cap fund?")
        assert "query" not in pipeline.log_fields(result.trace)

    def test_a_record_carrying_query_text_is_filtered_by_default(self) -> None:
        record = logging.LogRecord("mf_faq", logging.INFO, __file__, 1, "q", None, None)
        record.query = "my PAN is ABCDE1234F"
        assert pipeline.QueryTextFilter(log_queries=False).filter(record) is False

    def test_the_filter_passes_when_queries_are_explicitly_logged(self) -> None:
        record = logging.LogRecord("mf_faq", logging.INFO, __file__, 1, "q", None, None)
        record.query = "local debugging only"
        assert pipeline.QueryTextFilter(log_queries=True).filter(record) is True

    def test_logging_a_refusal_records_counts_and_no_identifier(self, capsys) -> None:
        logger = pipeline.configure_logging(log_queries=False)
        pipeline.answer("my PAN is ABCDE1234F")
        output = capsys.readouterr().out
        assert "pii_refusal" in output
        assert "ABCDE1234F" not in output

    def test_the_allowlist_excludes_the_assembled_context(self) -> None:
        result = pipeline.answer("What is the exit load on the HDFC flexi cap fund?")
        logged = pipeline.log_fields(result.trace)
        assert "context" not in logged
        assert logged["kind"] == "factual"

    def test_advice_is_refused_with_an_education_link_and_no_generator_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*args: object, **kwargs: object) -> DraftAnswer:
            raise AssertionError("a refusal must not reach a generator")

        monkeypatch.setattr(pipeline, "resolve_generator", explode)
        result = pipeline.answer("Should I buy the ELSS?")
        assert result.kind == "refusal"
        assert "amfiindia.com" in (result.citation_url or "")
        assert result.generator == "none"

    def test_a_performance_question_is_redirected_without_a_figure(self) -> None:
        result = pipeline.answer("What is the 1 year return of the large cap fund?")
        assert result.kind == "performance_redirect"
        assert "hdfc-large-cap-fund" in (result.citation_url or "")
        assert "%" not in result.text.replace("investor-education", "")

    def test_a_pan_is_refused_and_never_echoed(self) -> None:
        result = pipeline.answer("my PAN is ABCDE1234F, please check my folio")
        assert result.kind == "pii_refusal"
        assert "ABCDE1234F" not in str(result)
        assert "groww.in/help" in (result.citation_url or "")

    def test_an_out_of_corpus_question_is_refused_with_a_registered_link(self) -> None:
        reg: Registry = load_registry()
        result = pipeline.answer("What is the expense ratio of Parag Parflex?")
        assert result.kind == "out_of_corpus"
        assert result.citation_url in {source.url for source in reg.sources}

    def test_a_factual_question_returns_a_cited_answer(self) -> None:
        result = pipeline.answer("What is the exit load on the HDFC flexi cap fund?")
        assert result.kind == "factual"
        assert result.citation_url in {source.url for source in load_registry().sources}
        assert result.last_updated == "2026-09-27"
        assert len(guardrails.split_sentences(result.text)) <= 3
        assert isinstance(result, Answer)

    def test_the_answer_trace_is_json_serialisable(self) -> None:
        import json

        result = pipeline.answer("What is the minimum SIP for the large cap fund?")
        assert json.loads(json.dumps(result.trace, default=str))["generator"] in {
            "extractive",
            "llm",
        }

    def test_a_fact_the_corpus_lacks_is_reported_as_missing(self) -> None:
        """The corpus has no lock-in and no statement text, so the answer says so instead of guessing."""
        result = pipeline.answer("Is there a lock-in on the ELSS tax saver fund?")
        assert result.kind == "not_in_corpus"
        assert result.generator == "none"
        assert "hdfc-elss-tax-saver-fund" in (result.citation_url or "")
        assert result.retrieved == []
