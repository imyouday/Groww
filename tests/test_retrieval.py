"""Retrieval tests: the boost formula, MMR, the grounding gate and the assembly contract."""

from __future__ import annotations

import numpy as np
import pytest

from src import retrieval
from src.config import load_settings
from src.models import (
    AssembledContext,
    ChunkRecord,
    FactFamily,
    GateResult,
    Intent,
    ScoredChunk,
    SectionType,
)

SCHEME_NAME = "HDFC Large Cap Fund - Direct Growth"
PAGE_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


def make_chunk(
    text: str = "Expense ratio 1.03%",
    chunk_id: str = "c1",
    scheme_id: str = "S1",
    section: str = "Expense ratio and other fees",
    section_type: SectionType = SectionType.FEES,
    token_count: int = 100,
) -> ChunkRecord:
    """Build a synthetic stored chunk so scoring can be tested without the corpus."""
    return ChunkRecord(
        chunk_id=chunk_id,
        source_id=scheme_id,
        scheme_id=scheme_id,
        scheme_name=SCHEME_NAME if scheme_id == "S1" else f"Scheme {scheme_id}",
        section=section,
        section_type=section_type,
        text=text,
        embed_text=text,
        url=PAGE_URL,
        title="HDFC Large Cap Fund Direct Growth",
        fetched_at="2026-09-27",
        ordinal=0,
        token_count=token_count,
    )


def make_candidate(
    dense: float = 0.5,
    text: str = "Expense ratio 1.03%",
    chunk_id: str = "c1",
    scheme_id: str = "S1",
    section_type: SectionType = SectionType.FEES,
    token_count: int = 100,
) -> ScoredChunk:
    """Build a dense-stage candidate, the input boost() is specified against."""
    return ScoredChunk(
        chunk=make_chunk(text, chunk_id, scheme_id, section_type=section_type, token_count=token_count),
        dense=dense,
        keyword_boost=0.0,
        final=dense,
        mmr_selected=False,
    )


def test_boost_adds_exactly_the_architecture_formula() -> None:
    """architecture.md §11.4: 0.5 dense + 0.05 one fact term + 0.03 section match = 0.58."""
    boosted = retrieval.boost(
        [make_candidate(dense=0.5)], "expense ratio", FactFamily.EXPENSE_RATIO, None
    )
    assert boosted[0].keyword_boost == pytest.approx(0.08)
    assert boosted[0].final == pytest.approx(0.58)
    assert boosted[0].matched_terms == ["expense ratio"]


def test_boost_pays_more_for_each_additional_distinct_term() -> None:
    text = "Exit load 1.00% if redeemed within one year, and exit charges apply thereafter"
    boosted = retrieval.boost(
        [make_candidate(text=text, section_type=SectionType.GENERAL)],
        "exit load",
        FactFamily.EXIT_LOAD,
        None,
    )
    assert boosted[0].matched_terms == ["exit load", "exit charges"]
    assert boosted[0].keyword_boost == pytest.approx(0.10)


def test_a_term_contained_in_a_longer_term_is_not_double_counted() -> None:
    """"exit load" contains the word "load"; counting both would hand out the bonus for free."""
    boosted = retrieval.boost(
        [make_candidate(text="Exit load 1.00%", section_type=SectionType.GENERAL)],
        "exit load",
        FactFamily.EXIT_LOAD,
        None,
    )
    assert boosted[0].matched_terms == ["exit load"]
    assert boosted[0].keyword_boost == pytest.approx(0.05)


def test_boost_is_evidence_based_so_an_absent_term_earns_nothing() -> None:
    boosted = retrieval.boost(
        [make_candidate(text="Minimum investment amount is Rs 100", section_type=SectionType.GENERAL)],
        "expense ratio",
        FactFamily.EXPENSE_RATIO,
        None,
    )
    assert boosted[0].matched_terms == []
    assert boosted[0].keyword_boost == 0.0
    assert boosted[0].final == pytest.approx(0.5)


def test_a_repeated_term_counts_once_not_once_per_occurrence() -> None:
    """A measured bug: a chunk that repeats one term was paid the additional-term bonus twice."""
    text = (
        "Expense ratio is 1.03%. The expense ratio is disclosed in the factsheet. "
        "This expense ratio note repeats the label."
    )
    boosted = retrieval.boost(
        [make_candidate(text=text, section_type=SectionType.GENERAL)],
        "expense ratio",
        FactFamily.EXPENSE_RATIO,
        None,
    )
    assert boosted[0].matched_terms == ["expense ratio"]
    assert boosted[0].keyword_boost == pytest.approx(0.05)


def test_boost_adds_the_scheme_bonus_only_for_the_resolved_scheme() -> None:
    boosted = retrieval.boost(
        [make_candidate(), make_candidate(chunk_id="c2", scheme_id="S2")],
        "expense ratio",
        FactFamily.EXPENSE_RATIO,
        "S1",
    )
    by_id = {item.chunk.chunk_id: item for item in boosted}
    assert by_id["c1"].keyword_boost == pytest.approx(0.10)
    assert by_id["c2"].keyword_boost == pytest.approx(0.08)


def test_mmr_returns_the_requested_number_of_distinct_chunks() -> None:
    candidates = [make_candidate(chunk_id=f"c{i}", dense=0.9 - i / 100) for i in range(9)]
    selected = retrieval.mmr(candidates, 5, 0.3)
    assert len(selected) == 5
    assert len({item.chunk.chunk_id for item in selected}) == 5
    assert all(item.mmr_selected for item in selected)


def test_mmr_prefers_a_different_scheme_over_a_near_duplicate() -> None:
    """architecture.md §11.5: five near-identical fee tables must not collapse onto one scheme."""
    first = make_candidate(chunk_id="a", scheme_id="S1", dense=0.80)
    twin = make_candidate(chunk_id="b", scheme_id="S1", dense=0.79)
    other = make_candidate(chunk_id="c", scheme_id="S2", dense=0.70)
    vectors = {
        "a": np.array([1.0, 0.0], dtype="float32"),
        "b": np.array([0.999, 0.045], dtype="float32"),
        "c": np.array([0.0, 1.0], dtype="float32"),
    }
    selected = retrieval.mmr([first, twin, other], 2, 0.3, vectors)
    assert [item.chunk.chunk_id for item in selected] == ["a", "c"]


def test_mmr_falls_back_to_token_overlap_without_vectors() -> None:
    duplicate = "Expense ratio 1.03% charged on the assets of the scheme"
    near = "Expense ratio 1.03% charged on the assets of the scheme."
    distinct = "Exit load of one per cent applies within the first year"
    candidates = [
        make_candidate(chunk_id="a", text=duplicate, dense=0.80),
        make_candidate(chunk_id="b", text=near, dense=0.79),
        make_candidate(chunk_id="c", text=distinct, dense=0.70),
    ]
    selected = retrieval.mmr(candidates, 2, 0.3)
    assert [item.chunk.chunk_id for item in selected] == ["a", "c"]


def test_gate_fails_just_below_tau_and_passes_just_above() -> None:
    assert retrieval.grounding_gate(
        [make_candidate(dense=0.34)], FactFamily.EXPENSE_RATIO, False
    ).passed is False
    assert retrieval.grounding_gate(
        [make_candidate(dense=0.36)], FactFamily.EXPENSE_RATIO, False
    ).passed is True


def test_gate_threshold_honours_the_configured_value() -> None:
    settings = load_settings()
    assert settings.retrieval.gate_threshold == 0.35
    assert retrieval.grounding_gate(
        [make_candidate(dense=0.3499)], FactFamily.EXPENSE_RATIO, False
    ).passed is False


def test_unclassified_query_gets_the_extra_margin() -> None:
    settings = load_settings()
    at_tau = retrieval.grounding_gate([make_candidate(dense=0.36)], FactFamily.OTHER, False)
    stricter = retrieval.grounding_gate([make_candidate(dense=0.36)], FactFamily.OTHER, True)
    assert at_tau.passed is True
    assert stricter.passed is False
    assert stricter.threshold == pytest.approx(
        settings.retrieval.gate_threshold + settings.retrieval.unclassified_gate_margin
    )
    assert "below tau" in stricter.reason


def test_a_confident_but_wrong_fact_fails_term_coverage() -> None:
    """The classic failure: high similarity, different fact. Coverage is a hard AND-condition."""
    wrong_fact = make_candidate(
        dense=0.95,
        text="Minimum investment amount is Rs 100 and the fund is open for purchase",
        section_type=SectionType.FEES,
    )
    gate = retrieval.grounding_gate([wrong_fact], FactFamily.EXPENSE_RATIO, False)
    assert gate.passed is False
    assert "expense_ratio" in gate.reason


def test_gate_reports_the_terms_that_were_found() -> None:
    gate = retrieval.grounding_gate(
        [make_candidate(dense=0.5)], FactFamily.EXPENSE_RATIO, False
    )
    assert gate.passed is True
    assert "expense ratio" in gate.covered_terms


def test_gate_reports_an_empty_candidate_pool() -> None:
    gate = retrieval.grounding_gate([], FactFamily.EXPENSE_RATIO, False)
    assert gate.passed is False
    assert "no candidates" in gate.reason


def test_assemble_labels_and_numbers_each_chunk() -> None:
    text = retrieval.assemble([make_candidate()])
    assert text.startswith(
        f"[1] {SCHEME_NAME} | Expense ratio and other fees | source: {PAGE_URL}\n    "
    )


def test_assemble_drops_duplicate_text() -> None:
    first = make_candidate(chunk_id="a", text="Expense ratio 1.03%")
    twin = make_candidate(chunk_id="b", text="expense ratio 1.03 %")
    text = retrieval.assemble([first, twin])
    assert text.count("source:") == 1


def test_assemble_keeps_whole_chunks_within_the_budget_and_never_truncates_top_one() -> None:
    settings = load_settings()
    big = make_candidate(chunk_id="big", text="Expense ratio 1.03%", token_count=1500)
    same = make_candidate(chunk_id="same", text="Expense ratio 1.04%", token_count=1500)
    small = make_candidate(chunk_id="small", text="Expense ratio 1.05%", token_count=200)
    text = retrieval.assemble([big, same, small], settings)
    assert "big" not in text
    assert text.count("source:") == 2
    assert "1.03%" in text and "1.05%" in text


def test_a_single_oversized_top_chunk_is_still_included_whole() -> None:
    settings = load_settings()
    oversized = make_candidate(token_count=settings.retrieval.context_token_budget + 500)
    text = retrieval.assemble([oversized], settings)
    assert text.count("source:") == 1
    assert oversized.chunk.text in text


def test_contextualisation_injects_the_corpus_vocabulary() -> None:
    embedded = retrieval.contextualise_query(
        "exit load?", "S2", FactFamily.EXIT_LOAD
    )
    assert embedded.startswith("[")
    assert "exit load" in embedded
    assert embedded.endswith("exit load?")


def test_contextualisation_falls_back_to_a_generic_scheme_label() -> None:
    embedded = retrieval.contextualise_query("exit load?", None, FactFamily.EXIT_LOAD)
    assert "HDFC mutual fund" in embedded


def test_non_factual_intent_refuses_before_touching_the_store(monkeypatch) -> None:
    """PRD §5.3 and C3: no embedding, no search, no LLM for advice, performance, PII or smalltalk."""
    def explode(*_args, **_kwargs):
        raise AssertionError("retrieval ran for a non-factual intent")

    monkeypatch.setattr(retrieval, "embed_query", explode)
    monkeypatch.setattr(retrieval, "query", explode)
    for probe in (
        "Should I buy the flexi cap fund?",
        "Which of these gave the best 1-year return?",
        "my pan is ABCDE1234F",
        "What is the expense ratio of Parag Parflex?",
        "hello",
    ):
        result = retrieval.retrieve(probe)
        assert isinstance(result, GateResult)
        assert result.passed is False
        assert "refused fast" in result.reason


def test_retrieve_with_debug_returns_no_context_for_a_refused_intent() -> None:
    context, trace = retrieval.retrieve_with_debug("Should I buy the flexi cap fund?")
    assert context is None
    assert trace["intent"] == Intent.ADVICE_REQUEST.value
    assert trace["candidates"] == []
    assert trace["gate"]["passed"] is False
    assert trace["context"] == ""


def test_retrieve_with_debug_trace_carries_every_documented_field() -> None:
    _context, trace = retrieval.retrieve_with_debug(
        "What is the expense ratio of the HDFC Large Cap fund?"
    )
    for key in (
        "query",
        "intent",
        "matched_rule",
        "scheme_id",
        "fact_family",
        "needs_evidence",
        "tau",
        "embedded_query",
        "candidates",
        "mmr_picks",
        "gate",
        "context",
        "context_tokens",
        "total_ms",
    ):
        assert key in trace
    assert trace["candidates"]
    assert set(trace["candidates"][0]) >= {
        "chunk_id",
        "scheme",
        "section",
        "dense",
        "boost",
        "final",
        "terms",
    }
    assert len(trace["mmr_picks"]) == len(set(trace["mmr_picks"]))


def test_expense_ratio_query_returns_the_fee_chunk_for_the_named_scheme() -> None:
    context = retrieval.retrieve("What is the expense ratio of the HDFC Large Cap fund?")
    assert not isinstance(context, GateResult)
    assert context.scheme_id == "S1"
    assert context.fact_family is FactFamily.EXPENSE_RATIO
    top = context.chunks[0]
    assert top.chunk.scheme_id == "S1"
    assert "expense ratio" in top.chunk.text.lower()
    assert context.total_tokens <= load_settings().retrieval.context_token_budget


def test_lock_in_query_resolves_the_scheme_even_though_the_gate_refuses() -> None:
    """The corpus holds no lock-in text, so the gate must refuse. The scheme filter must still apply."""
    context, trace = retrieval.retrieve_with_debug("Is there a lock-in on the ELSS tax saver fund?")
    assert context is None
    assert trace["scheme_id"] == "S3"
    assert {item["scheme"] for item in trace["candidates"]} == {"S3"}


def test_an_unclassified_question_is_gated_at_the_raised_threshold() -> None:
    _context, trace = retrieval.retrieve_with_debug("what is the ticker symbol of the fund")
    assert trace["needs_evidence"] is True
    assert trace["gate"]["threshold"] == pytest.approx(0.45)


def test_known_gap_a_global_tau_does_not_separate_out_of_corpus_questions() -> None:
    """Measured in Phase 6 and pinned deliberately: τ alone does not reject off-topic questions.

    Raw MiniLM cosine over this corpus is compressed into a narrow high band. In-corpus
    fact-family questions score 0.70-0.86; questions the corpus cannot answer at all score
    0.48-0.71, so every off-topic probe below clears τ + margin = 0.45:

        "what is the ticker symbol of the fund"          0.71
        "what is the recipe for chocolate cake"           0.60
        "how do I bake sourdough bread at high altitude"  0.49

    For those questions FactFamily.OTHER has no synonym set, so term coverage cannot apply and
    the score is the only defence. The safety that does hold is coverage on the fact families
    that carry one: an "expense ratio" question only passes if a retrieved chunk literally
    contains expense-ratio vocabulary. architecture.md §12 makes the τ sweep a Phase 11
    deliverable, and this measurement is the first row of that sweep.
    """
    for probe in (
        "what is the ticker symbol of the fund",
        "what is the recipe for chocolate cake",
        "how do I bake sourdough bread at high altitude",
    ):
        result = retrieval.retrieve(probe)
        assert isinstance(result, AssembledContext), probe
        assert result.top_score > 0.45, probe


def test_term_coverage_is_what_rejects_a_topically_close_wrong_fact() -> None:
    """The fact families that carry synonyms keep their protection even though τ does not."""
    result = retrieval.retrieve("what is the exit load on the HDFC Large Cap fund")
    assert isinstance(result, AssembledContext)
    assert any(
        "exit load" in item.chunk.text.lower() or "exit charges" in item.chunk.text.lower()
        for item in result.chunks
    )
