"""Chunking tests: unit splitting, the overlap rule, determinism, and the encoder token bound.

The overlap tests matter more than they look. A 60-token overlap on a fee table produces a
chunk whose first rows are a copy of the previous chunk's last rows, which reads to a generator
as a different set of fees. That is the specific failure PRD §9.3 calls out, so "no overlap on
FEES" is asserted directly rather than inferred from a token count.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from src.chunking import (
    FALLBACK_HEADING,
    Variant,
    chunk_all,
    chunk_document,
    chunk_document_with_stats,
    chunk_stats,
    classify_section,
    drop_boilerplate,
    effective_bounds,
    make_token_counter,
    merge_small_sections,
    model_token_ceiling,
    parse_sections,
    split_into_units,
    write_chunks_jsonl,
)
from src.config import load_settings
from src.loading import load_all
from src.models import LoadedDoc, SectionType, SourceRecord, SourceType
from src.registry import load_registry

FEE_PAGE = """
Min. for SIP

Rs 100

## Minimum investments

Min. for 1st investment

Rs 100

Min. for SIP

Rs 100

## Expense ratio and other fees

| Plan | Direct | Indirect | Total |
| --- | --- | --- | --- |
| Direct Growth | 1.03% | 0.19% | 1.22% |
| Direct Plan | 1.28% | 0.19% | 1.47% |

## Exit load

Exit load of 1% if redeemed within 1 year.

Exit load of 0.5% if redeemed after 1 year but before 2 years.

Exit load is nil after 2 years.
"""

PROSE_PAGE = """
## About the fund

The fund follows a multi-cap strategy and invests across market capitalisation segments. It
maintains a portfolio of equity instruments whose composition is reviewed each quarter. The
mandate is to generate long-term capital appreciation from a diversified equity exposure. Risk
is managed through diversification rather than through a single-factor bet. The scheme was
launched in 1999 and has completed more than two decades of operation. Its assets are spread
across financials, industrials, and information technology holdings. The fund manager reviews
each holding against a valuation framework before adding to the portfolio. Dividends declared
by the holdings accrue to the scheme and are distributed as per the distribution policy. The
portfolio turnover is monitored to keep transaction costs contained. Investment decisions are
taken by a dedicated research team that publishes its house view quarterly.

## Objective

The scheme seeks to provide long-term capital appreciation by investing in a diversified
portfolio of equity securities across the market capitalisation spectrum.
"""

BOILERPLATE_PAGE = """
## Disclosures

Mutual fund investments are subject to market risks. Read all scheme related documents
carefully before investing. Download app to start investing today with quick paperwork.

## About the fund

The fund invests in a diversified equity portfolio and reviews its composition each quarter.
"""


def make_source(source_id: str = "S1", scheme_name: str = "HDFC Large Cap Fund - Direct Growth") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        scheme_id=source_id,
        scheme_name=scheme_name,
        source_type=SourceType.SCHEME_PAGE,
        title=f"{scheme_name} fund page",
        url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        publisher="HDFC AMC (via Groww)",
        allowed_for_citation=True,
        fetched_at="2026-09-27",
    )


def make_doc(text: str, source_id: str = "S1", scheme_name: str = "HDFC Large Cap Fund - Direct Growth") -> LoadedDoc:
    return LoadedDoc(
        source=make_source(source_id, scheme_name),
        raw_path=f"data/raw/{source_id}.html",
        text_path=f"data/processed/{source_id}.txt",
        text=text.strip(),
        char_count=len(text),
        redaction_hits=0,
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9.%()₹/-]+", text.lower())


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def counter(settings):
    """The real tokenizer, so token bounds are the encoder's and not an estimate."""
    return make_token_counter(settings)


@pytest.fixture(scope="module")
def small_settings(settings):
    """Settings whose bounds are small enough that a test can build a real multi-chunk page."""
    return replace(
        settings,
        chunking=replace(settings.chunking, max_tokens=120, min_tokens=40, overlap_tokens=20),
    )


def test_leading_prose_gets_the_overview_heading() -> None:
    sections = parse_sections("Min. for SIP\n\nRs 100\n\n## Fees\n\nExit load is nil.")
    assert [section.heading for section in sections] == ["Overview", "Fees"]


def test_a_label_and_its_value_are_one_unit_not_two() -> None:
    section = parse_sections("Min. for SIP\n\nRs 100\n\nExpense ratio\n\n1.03%")[0]
    units = split_into_units(section)
    assert [unit.text for unit in units] == ["Min. for SIP\nRs 100", "Expense ratio\n1.03%"]


def test_a_table_is_one_unit_even_when_blank_lines_separate_its_rows() -> None:
    body = "| Plan | Fee |\n| --- | --- |\n| Direct Growth | 1.03% |\n| Direct Plan | 1.28% |"
    units = split_into_units(parse_sections(f"## Fees\n\n{body}")[0])
    assert len(units) == 1
    assert units[0].header_row == "| Plan | Fee |"
    assert units[0].text.count("\n") == 3


def test_a_sentence_line_is_not_mistaken_for_a_labelled_fact() -> None:
    section = parse_sections("The exit load of 1% applies within the first year of redemption.")[0]
    units = split_into_units(section)
    assert len(units) == 1
    assert units[0].kind.value == "paragraph"


def test_classify_section_reads_the_heading_first() -> None:
    assert classify_section("Expense ratio and other fees", "some body") is SectionType.FEES
    assert classify_section("Exit load, stamp duty and tax", "some body") is SectionType.TAX
    assert classify_section("Investment Objective", "some body") is SectionType.RISK
    assert classify_section("Fund management", "some body") is SectionType.GENERAL


def test_classify_section_falls_back_to_the_body_for_a_vague_heading() -> None:
    body = "Exit load of 1% if redeemed within 1 year."
    assert classify_section("Details", body) is SectionType.FEES


def test_the_fee_table_lands_in_one_chunk_with_no_overlap(small_settings, counter) -> None:
    chunks = chunk_document(make_doc(FEE_PAGE), small_settings, counter)
    fee_chunks = [chunk for chunk in chunks if chunk.section_type is SectionType.FEES]
    assert fee_chunks
    table_chunks = [chunk for chunk in fee_chunks if "| Plan |" in chunk.text]
    assert len(table_chunks) == 1
    assert table_chunks[0].text.count("\n| ") == 3
    assert table_chunks[0].token_count <= effective_bounds(None, small_settings, counter)[0]


def test_prose_overlap_repeats_a_tail_within_the_configured_budget(small_settings, counter) -> None:
    chunks = chunk_document(make_doc(PROSE_PAGE), small_settings, counter)
    assert len(chunks) > 1
    first, second = chunks[0], chunks[1]
    shared = set(first.text.split("\n")) & set(second.text.split("\n"))
    assert shared, "prose chunks must share a tail when overlap is enabled"
    overlap_tokens = counter.count("\n".join(sorted(shared)))
    assert abs(overlap_tokens - small_settings.chunking.overlap_tokens) <= 10


def test_a_tax_section_never_gets_overlap(small_settings, counter) -> None:
    sentences = [
        f"If you redeem after {months} months the gains on this scheme are taxed at {30 - months // 4} percent."
        for months in range(1, 25)
    ]
    text = "## Tax implication\n\n" + " ".join(sentences * 2)
    chunks = chunk_document(make_doc(text), small_settings, counter)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.section_type is SectionType.TAX
    for first, second in zip(chunks, chunks[1:], strict=False):
        assert not set(first.text.split("\n")) & set(second.text.split("\n"))


def test_boilerplate_is_dropped(small_settings, counter) -> None:
    boiler = parse_sections(BOILERPLATE_PAGE)[0]
    assert drop_boilerplate(boiler, classify_section(boiler.heading, boiler.body), counter) is True
    chunks = chunk_document(make_doc(BOILERPLATE_PAGE), small_settings, counter)
    assert all("Download app" not in chunk.text for chunk in chunks)
    assert all("Mutual fund investments are subject" not in chunk.text for chunk in chunks)


def test_a_section_with_a_heading_but_no_body_is_dropped(small_settings, counter) -> None:
    section = parse_sections("## About us\n\n## References\n\nNo content follows either heading.")[0]
    assert drop_boilerplate(section, SectionType.GENERAL, counter) is True


def test_a_thin_digitless_section_is_dropped_but_a_fee_section_is_not(small_settings, counter) -> None:
    thin = parse_sections("## Overview\n\nHDFC Mutual Fund")[0]
    assert drop_boilerplate(thin, SectionType.GENERAL, counter) is True
    fee = parse_sections("## Exit load\n\nExit load of 1% if redeemed within 1 year")[0]
    assert drop_boilerplate(fee, SectionType.FEES, counter) is False
    numbered = parse_sections("## Fund age\n\nLaunched in 1999")[0]
    assert drop_boilerplate(numbered, SectionType.GENERAL, counter) is False


def test_chunk_ids_are_deterministic_across_runs(small_settings, counter) -> None:
    doc = make_doc(FEE_PAGE)
    first = [chunk.chunk_id for chunk in chunk_document(doc, small_settings, counter)]
    second = [chunk.chunk_id for chunk in chunk_document(doc, small_settings, counter)]
    assert first == second


def test_the_same_heading_at_a_different_ordinal_gets_a_different_id(settings, counter) -> None:
    unmerged = replace(settings, chunking=replace(settings.chunking, merge_small_sections=False))
    doc = make_doc(
        "## Exit load\n\nExit load of 1% if redeemed within 1 year.\n\n"
        "## Exit load\n\nExit load of 0.5% if redeemed after 1 year but before 2 years."
    )
    chunks = chunk_document(doc, unmerged, counter)
    assert len(chunks) == 2
    assert chunks[0].section == chunks[1].section
    assert chunks[0].ordinal == 0 and chunks[1].ordinal == 1
    assert chunks[0].chunk_id != chunks[1].chunk_id


def test_the_context_header_is_embedded_but_not_shown(small_settings, counter) -> None:
    chunks = chunk_document(make_doc(FEE_PAGE), small_settings, counter)
    assert chunks
    for chunk in chunks:
        assert chunk.embed_text.startswith("[HDFC Large Cap Fund - Direct Growth]")
        assert not chunk.text.startswith("[HDFC")
        assert chunk.embed_text == f"[{chunk.scheme_name}] {chunk.section}\n{chunk.text}"


def test_an_oversized_table_is_split_by_row_groups_with_the_header_repeated(
    small_settings, counter
) -> None:
    rows = "\n".join(f"| Stock {index} | Sector | Equity | {index}.{index}% |" for index in range(40))
    text = f"## Holdings\n\n| Name | Sector | Instruments | Assets |\n| --- | --- | --- | --- |\n{rows}"
    chunks = chunk_document(make_doc(text), small_settings, counter)
    table_chunks = [chunk for chunk in chunks if "| Stock" in chunk.text]
    assert len(table_chunks) > 1
    for chunk in table_chunks:
        assert chunk.text.startswith("| Name | Sector | Instruments | Assets |")
        assert all(line.startswith("|") for line in chunk.text.split("\n"))


def test_a_merge_keeps_both_headings(small_settings, counter) -> None:
    text = "## Minimum investments\n\nMin. for SIP\n\nRs 100\n\n## Exit load\n\nExit load of 1%."
    chunks = chunk_document(make_doc(text), small_settings, counter)
    sections = [chunk.section for chunk in chunks]
    assert any("Minimum investments" in section and "Exit load" in section for section in sections)


def test_a_merge_refuses_to_mix_a_table_with_labelled_facts(small_settings, counter) -> None:
    rows = "\n".join(f"| Stock {index} | Sector | Equity | {index}.{index}% |" for index in range(30))
    text = f"## Holdings\n\n| Name | Sector | Instruments | Assets |\n{rows}\n\n## Min. for SIP\n\nRs 100"
    chunks = chunk_document(make_doc(text), small_settings, counter)
    for chunk in chunks:
        has_table = any(line.startswith("|") for line in chunk.text.split("\n"))
        has_fact = "Rs 100" in chunk.text
        assert not (has_table and has_fact)


def test_merging_counts_its_merges(small_settings, counter) -> None:
    from src.chunking import _section_chunks

    first = parse_sections("## Exit load\n\nExit load of 1%.")[0]
    second = parse_sections("## Minimum investments\n\nMin. for SIP\n\nRs 100")[0]
    drafts = _section_chunks(first, SectionType.FEES, 120, 0, counter)[0]
    drafts += _section_chunks(second, SectionType.FEES, 120, 0, counter)[0]
    merged, count = merge_small_sections(drafts, 40, 120, counter)
    assert count == 1
    assert len(merged) == 1
    assert "Exit load" in merged[0].heading and "Minimum investments" in merged[0].heading


def test_a_merge_that_would_exceed_max_tokens_is_refused(small_settings, counter) -> None:
    from src.chunking import DraftChunk

    big = "Exit load of 1%. " * 12
    drafts = [
        DraftChunk("Exit load", SectionType.FEES, big, ("Exit load",)),
        DraftChunk("Minimum investments", SectionType.FEES, "Min. for SIP\nRs 100", ("Minimum investments",)),
    ]
    merged, count = merge_small_sections(drafts, 40, 120, counter)
    assert counter.count(big) + 6 <= 120 or count == 0
    if counter.count(big) > 100:
        assert count == 0
        assert len(merged) == 2
    else:
        assert count == 1


def test_a_merge_across_two_different_section_types_is_refused(small_settings, counter) -> None:
    from src.chunking import DraftChunk

    drafts = [
        DraftChunk("Exit load", SectionType.FEES, "Exit load of 1%. " * 8, ("Exit load",)),
        DraftChunk("Tax implication", SectionType.TAX, "If you redeem within one year, tax applies.", ("Tax implication",)),
    ]
    merged, count = merge_small_sections(drafts, 40, 120, counter)
    assert count == 0
    assert len(merged) == 2


def test_dropped_and_merged_counts_are_reported(small_settings, counter) -> None:
    doc = make_doc(BOILERPLATE_PAGE + "\n\n" + PROSE_PAGE)
    _, plan = chunk_document_with_stats(doc, small_settings, counter)
    # One for the "Disclosures" promo phrase and one for the thin, digitless about-section.
    assert plan["dropped_sections"] == 2
    assert plan["merged_chunks"] >= 0
    assert plan["deduped_chunks"] >= 0


def test_no_chunk_exceeds_the_encoder_bound(small_settings, counter) -> None:
    for page in (FEE_PAGE, PROSE_PAGE, BOILERPLATE_PAGE):
        for variant in (None, *Variant):
            bound = effective_bounds(variant, small_settings, counter)[0]
            for chunk in chunk_document(make_doc(page), small_settings, counter, variant):
                assert chunk.token_count <= bound, f"{variant} produced {chunk.token_count}"


def test_bounds_come_from_config_and_are_clamped_to_the_encoder(settings, counter) -> None:
    ceiling = counter.max_tokens
    assert effective_bounds(None, settings, counter) == (
        min(settings.chunking.max_tokens, ceiling),
        settings.chunking.overlap_tokens,
    )
    assert effective_bounds(Variant.SEMANTIC_350, settings, counter) == (
        min(350, ceiling),
        60,
    )
    assert effective_bounds(Variant.SEMANTIC_600, settings, counter) == (
        min(600, ceiling),
        60,
    )
    assert effective_bounds(Variant.FIXED_512, settings, counter) == (
        min(512, ceiling),
        50,
    )


def test_an_abbreviation_is_not_a_sentence_boundary() -> None:
    from src.chunking import _split_sentences

    assert _split_sentences("Mr. Dhruv has done B.Com, CA and CFA") == [
        "Mr. Dhruv has done B.Com, CA and CFA"
    ]
    assert _split_sentences("HDFC Mutual Fund. Exit load is 1%.") == [
        "HDFC Mutual Fund.",
        "Exit load is 1%.",
    ]


def test_the_configured_bound_of_600_is_clamped_to_the_models_real_ceiling(settings, counter) -> None:
    assert settings.chunking.max_tokens == 600
    assert model_token_ceiling(settings.embedding.model_id) == 256
    assert counter.max_tokens == 256 - 2
    assert effective_bounds(None, settings, counter)[0] == 254


def test_the_ceiling_comes_from_the_model_not_the_tokenizer() -> None:
    """The tokenizer says 512 and the model says 256; the model's answer is the one that matters.

    Clamping to the tokenizer's 512 produced 510-token chunks whose second half the encoder
    discarded, which is why the corpus first chunked to 68 pieces instead of 107.
    """
    settings = load_settings()
    assert settings.chunking.max_tokens > model_token_ceiling(settings.embedding.model_id)


def test_the_fixed_variant_ignores_section_boundaries(settings, counter) -> None:
    def paragraph(index: int) -> str:
        return " ".join(
            f"The fund reviews holding {index}.{item} against its valuation framework each quarter."
            for item in range(8)
        )

    doc = make_doc(
        "\n\n".join(
            (
                f"## About the fund\n\n{paragraph(0)}",
                f"## Investment objective\n\n{paragraph(1)}",
                f"## Investment strategy\n\n{paragraph(2)}",
            )
        )
    )
    semantic = chunk_document(doc, settings, counter)
    fixed = chunk_document(doc, settings, counter, Variant.FIXED_512)
    assert len(semantic) == 3
    assert [chunk.section for chunk in semantic] == [
        "About the fund",
        "Investment objective",
        "Investment strategy",
    ]
    assert all(chunk.section_type is SectionType.GENERAL for chunk in fixed)
    assert all(chunk.section == FALLBACK_HEADING for chunk in fixed)
    assert any(len(chunk.text.split("\n")) > 1 for chunk in fixed)


def test_an_oversized_paragraph_keeps_every_sentence(small_settings, counter) -> None:
    sentences = [
        f"Sentence number {index} states that the fund holds {index + 7} holdings in the portfolio."
        for index in range(12)
    ]
    doc = make_doc("## About the fund\n\n" + " ".join(sentences))
    chunks = chunk_document(doc, small_settings, counter)
    assert len(chunks) > 1
    joined = " ".join(chunk.text for chunk in chunks)
    for sentence in sentences:
        assert sentence in joined, "splitting a paragraph must not drop a sentence"


def test_stats_report_what_the_ablation_needs(counter) -> None:
    chunks = chunk_document(make_doc(FEE_PAGE + PROSE_PAGE), load_settings(), counter)
    stats = chunk_stats(chunks)
    assert stats["count"] == len(chunks)
    assert 0 < int(stats["median_tokens"]) <= int(stats["max_tokens"])
    assert set(stats["by_section_type"]) <= {item.value for item in SectionType}
    assert chunk_stats([])["count"] == 0


def test_jsonl_round_trips_every_field(tmp_path) -> None:
    settings = load_settings()
    chunks = chunk_document(make_doc(FEE_PAGE), settings, make_token_counter(settings))
    path = write_chunks_jsonl(chunks, tmp_path / "chunks.jsonl")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(chunks)
    for line, chunk in zip(lines, chunks, strict=True):
        assert line["chunk_id"] == chunk.chunk_id
        assert line["section_type"] == chunk.section_type.value
        assert line["token_count"] == chunk.token_count
        assert line["text"] == chunk.text


def test_every_fact_family_is_retrievable_for_every_scheme(settings, counter) -> None:
    """implementation.md Phase 4 DoD: each fact family must survive chunking, for all 5 schemes.

    The failure this catches is silent: a boilerplate rule that widens by one word, or a
    label-pairing change, removes a fact family from the corpus while the chunk count, the median
    and the tests all stay green.
    """
    families = (
        "expense ratio",
        "exit load",
        "aum",
        "min. for sip",
        "stamp duty",
        "risk",
        "holdings",
    )
    docs, _ = load_all(load_registry(settings), settings)
    assert len(docs) >= 5
    for doc in docs:
        blob = " ".join(
            chunk.embed_text.lower() for chunk in chunk_document(doc, settings, counter)
        )
        for family in families:
            assert family in blob, f"{doc.source.source_id} lost the {family} family"


def test_no_chunk_in_the_real_corpus_exceeds_the_model_ceiling(settings, counter) -> None:
    bound = effective_bounds(None, settings, counter)[0]
    docs, _ = load_all(load_registry(settings), settings)
    for doc in docs:
        for chunk in chunk_document(doc, settings, counter):
            assert chunk.token_count <= bound, f"{doc.source.source_id} produced {chunk.token_count}"


def test_a_whole_short_section_is_kept_because_it_is_the_answer(small_settings, counter) -> None:
    """S3's exit load is the two words "Exit load" and "Nil".

    A fragment floor applied to every chunk deleted it, which is a fact removed from the corpus
    because it was short, and a floor cannot tell that difference on its own.
    """
    doc = make_doc("## Exit load\n\nNil")
    chunks = chunk_document(doc, small_settings, counter)
    assert len(chunks) == 1
    assert chunks[0].section == "Exit load"
    assert chunks[0].text == "Nil"


def test_a_split_section_never_emits_a_fragment(small_settings, counter) -> None:
    """A section that splits must not leave a below-floor draft behind.

    The "Education" case on S1 was exactly this: a mis-paired label became its own one-token unit
    and, once the section split, its own one-token chunk. A section that does not split keeps its
    single draft whatever its size, which is what keeps S3's "Exit load / Nil" retrievable.
    """
    from src.chunking import MIN_CHUNK_TOKENS

    doc = make_doc("## Manager\n\nEducation\n\n" + "The manager has managed funds since 1999. " * 20)
    chunks = chunk_document(doc, small_settings, counter)
    assert len(chunks) > 1
    assert all(counter.count(chunk.text) >= MIN_CHUNK_TOKENS for chunk in chunks)


def test_no_kept_section_body_is_lost_on_the_real_corpus(settings, counter) -> None:
    """Every word of every kept section body must appear in some chunk.

    This is the test that catches silent truncation. The first splitter stopped packing once a
    paragraph hit the budget and discarded the rest of it, which cost S1 six of thirteen sentences
    of the fund mandate while every other test still passed, because the count and the median
    looked healthy.
    """
    docs, _ = load_all(load_registry(settings), settings)
    assert docs
    for doc in docs:
        kept = [
            section
            for section in parse_sections(doc.text)
            if not drop_boilerplate(
                section, classify_section(section.heading, section.body), counter
            )
        ]
        body = Counter(_words(" ".join(section.body for section in kept)))
        chunks = Counter(_words(" ".join(chunk.text for chunk in chunk_document(doc, settings, counter))))
        lost = {word: count - chunks.get(word, 0) for word, count in body.items() if count > chunks.get(word, 0)}
        assert not lost, f"{doc.source.source_id} lost {lost}"


def test_no_duplicate_text_survives_the_dedupe(counter) -> None:
    settings = load_settings()
    repeated = "## About the fund\n\n" + ("The fund invests across market capitalisation segments. " * 3)
    chunks = chunk_document(make_doc(repeated + "\n\n" + repeated), settings, counter)
    assert len({chunk.text for chunk in chunks}) == len(chunks)


def test_the_committed_chunks_dump_matches_a_fresh_run(settings, counter) -> None:
    path = settings.paths.resolve("chunks_dump")
    if not Path(path).is_file():
        pytest.skip("data/chunks.jsonl has not been generated yet")
    stored = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    fresh, warnings = chunk_all(load_registry(settings), settings)
    assert warnings == []
    assert len(stored) == len(fresh)
    assert [line["chunk_id"] for line in stored] == [chunk.chunk_id for chunk in fresh]
