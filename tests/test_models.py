"""Tests for the shared stage contracts in src/models.py (architecture.md §6.2)."""

from __future__ import annotations

import dataclasses

import pytest

from src.config import load_settings
from src.models import (
    Answer,
    AssembledContext,
    BuildReport,
    ChunkRecord,
    DraftAnswer,
    FactFamily,
    GateResult,
    Intent,
    LoadedDoc,
    ParseEmptyError,
    PipelineError,
    ScoredChunk,
    SectionType,
    SourceFetchError,
    SourceNotAllowed,
    SourceRecord,
    SourceType,
    IndexNotBuiltError,
)

STAGE_CONTRACTS = (
    SourceRecord,
    LoadedDoc,
    ChunkRecord,
    ScoredChunk,
    AssembledContext,
    DraftAnswer,
    Answer,
    GateResult,
    BuildReport,
)


def _chunk() -> ChunkRecord:
    return ChunkRecord(
        chunk_id="abc123",
        source_id="S1",
        scheme_id="S1",
        scheme_name="HDFC Large Cap Fund - Direct Growth",
        section="Expense ratio and other fees",
        section_type=SectionType.FEES,
        text="Expense ratio: 0.35%",
        embed_text="[HDFC Large Cap Fund - Direct Growth] Expense ratio and other fees\nExpense ratio: 0.35%",
        url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        title="HDFC Large Cap Fund",
        fetched_at="2026-09-27",
        ordinal=0,
        token_count=12,
    )


@pytest.mark.parametrize("cls", STAGE_CONTRACTS, ids=lambda c: c.__name__)
def test_stage_contracts_are_frozen(cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is True


@pytest.mark.parametrize("cls", STAGE_CONTRACTS, ids=lambda c: c.__name__)
def test_stage_contracts_are_annotated(cls: type) -> None:
    for f in dataclasses.fields(cls):
        assert f.type, f"{cls.__name__}.{f.name} has no type annotation"


def test_frozen_contracts_reject_mutation() -> None:
    chunk = _chunk()
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "tampered"  # type: ignore[misc]


def test_answer_contract_fields_match_architecture_section_6_2() -> None:
    assert [f.name for f in dataclasses.fields(Answer)] == [
        "text",
        "kind",
        "citation_url",
        "citation_source_id",
        "last_updated",
        "generator",
        "retrieved",
        "trace",
    ]


def test_chunk_record_fields_match_architecture_section_6_2() -> None:
    assert [f.name for f in dataclasses.fields(ChunkRecord)] == [
        "chunk_id",
        "source_id",
        "scheme_id",
        "scheme_name",
        "section",
        "section_type",
        "text",
        "embed_text",
        "url",
        "title",
        "fetched_at",
        "ordinal",
        "token_count",
    ]


def test_gate_result_fields_match_architecture_section_6_2() -> None:
    assert [f.name for f in dataclasses.fields(GateResult)] == [
        "passed",
        "top_score",
        "threshold",
        "reason",
        "covered_terms",
    ]


def test_build_report_fields_match_implementation_phase_1() -> None:
    assert [f.name for f in dataclasses.fields(BuildReport)] == [
        "sources_ok",
        "sources_failed",
        "chunk_count",
        "chunk_stats",
        "warnings",
        "duration_s",
        "config_hash",
        "corpus_hash",
    ]


def test_draft_answer_defaults_are_not_shared() -> None:
    first = DraftAnswer(text="a", generator="extractive")
    second = DraftAnswer(text="b", generator="llm")
    first.sentinels.append("REFUSE")
    assert second.sentinels == []
    assert first.raw_model_output is None


@pytest.mark.parametrize(
    ("enum_cls", "expected"),
    [
        (
            SourceType,
            {"scheme_page", "factsheet", "kim", "sid", "fee_page", "education"},
        ),
        (SectionType, {"fees", "lock_in", "risk", "tax", "general"}),
            (
                FactFamily,
                {
                    "expense_ratio",
                    "exit_load",
                    "min_sip",
                    "lock_in",
                    "riskometer",
                    "benchmark",
                    "statements",
                    "nav",
                    "aum",
                    "cutoff_time",
                    "direct_vs_regular",
                    "growth_vs_idcw",
                    "kyc",
                    "nominee",
                    "other",
                },
            ),
        (
            Intent,
            {
                "factual_fact",
                "advice_request",
                "performance_request",
                "pii_request",
                "out_of_corpus",
                "smalltalk",
            },
        ),
    ],
    ids=lambda v: getattr(v, "__name__", ""),
)
def test_enum_values(enum_cls: type, expected: set[str]) -> None:
    assert {member.value for member in enum_cls} == expected
    assert all(isinstance(member, str) for member in enum_cls)


def test_fact_terms_config_keys_all_resolve_to_fact_families() -> None:
    fact_terms = load_settings().retrieval.fact_terms
    known = {family.value for family in FactFamily}
    assert set(fact_terms) <= known


def test_exception_hierarchy() -> None:
    for exc in (SourceNotAllowed, SourceFetchError, ParseEmptyError, IndexNotBuiltError):
        assert issubclass(exc, PipelineError)
    assert issubclass(PipelineError, Exception)
