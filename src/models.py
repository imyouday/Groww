"""Shared data contracts for every pipeline stage.

Every stage boundary is a frozen dataclass declared here. Stages import these types and
never return raw tuples or dicts, and never mutate their input (architecture.md §6.2).

This module is infrastructure: it imports no other module in the project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class SourceType(str, Enum):
    SCHEME_PAGE = "scheme_page"
    FACTSHEET = "factsheet"
    KIM = "kim"
    SID = "sid"
    FEE_PAGE = "fee_page"
    EDUCATION = "education"


class SectionType(str, Enum):
    FEES = "fees"
    LOCK_IN = "lock_in"
    RISK = "risk"
    TAX = "tax"
    GENERAL = "general"


class FactFamily(str, Enum):
    EXPENSE_RATIO = "expense_ratio"
    EXIT_LOAD = "exit_load"
    MIN_SIP = "min_sip"
    LOCK_IN = "lock_in"
    RISKOMETER = "riskometer"
    BENCHMARK = "benchmark"
    STATEMENTS = "statements"
    OTHER = "other"


class Intent(str, Enum):
    FACTUAL_FACT = "factual_fact"
    ADVICE_REQUEST = "advice_request"
    PERFORMANCE_REQUEST = "performance_request"
    PII_REQUEST = "pii_request"
    OUT_OF_CORPUS = "out_of_corpus"
    SMALLTALK = "smalltalk"


class PipelineError(Exception):
    """Base class for every error this project raises deliberately."""


class SourceNotAllowed(PipelineError):
    """A URL was requested that is not on the public-source allowlist (constraint C1)."""


class SourceFetchError(PipelineError):
    """A registered public source could not be retrieved."""


class ParseEmptyError(PipelineError):
    """A page was retrieved but yielded too little text to be usable."""


class IndexNotBuiltError(PipelineError):
    """The vector store has no collection or no chunks, so queries cannot be served."""


class ModelNotCachedError(PipelineError):
    """The encoder weights are not on this machine and cannot be fetched."""


class GenerationError(PipelineError):
    """The LLM generator could not produce a draft; guardrails decide how to degrade."""


@dataclass(frozen=True)
class SourceRecord:
    """One registered public source page. Row of data/sources.csv (architecture.md §6.1)."""

    source_id: str
    scheme_id: str
    scheme_name: str
    source_type: SourceType
    title: str
    url: str
    publisher: str
    allowed_for_citation: bool
    fetched_at: str
    content_hash: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class LoadedDoc:
    """A source after fetching and cleaning, with PII already redacted (stage 1 output)."""

    source: SourceRecord
    raw_path: str
    text_path: str
    text: str
    char_count: int
    redaction_hits: int


@dataclass(frozen=True)
class ChunkRecord:
    """A retrievable unit of corpus text (stage 2 output)."""

    chunk_id: str
    source_id: str
    scheme_id: str
    scheme_name: str
    section: str
    section_type: SectionType
    text: str
    embed_text: str
    url: str
    title: str
    fetched_at: str
    ordinal: int
    token_count: int


@dataclass(frozen=True)
class ScoredChunk:
    """A candidate chunk with its similarity, boosts and selection state (stage 5 output)."""

    chunk: ChunkRecord
    dense: float
    keyword_boost: float
    final: float
    mmr_selected: bool
    matched_terms: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssembledContext:
    """Ordered, deduplicated, budget-capped chunks handed to the generator.

    `context_text` is the rendered form of `chunks`, produced by the assembling stage. The LLM
    generator reads it rather than importing the retrieval stage to re-render the block: a stage
    may not depend on another policy stage (architecture.md §5.2), and re-deriving the block here
    would duplicate the ordering and token-budget rules that decide which text may be shown.
    """

    query: str
    scheme_id: str | None
    fact_family: FactFamily
    chunks: list[ScoredChunk]
    total_tokens: int
    top_score: float
    context_text: str = ""


@dataclass(frozen=True)
class DraftAnswer:
    """Unvalidated generator output. Never rendered; guardrails.convert_answer is the only path
    from this to a user-visible Answer."""

    text: str
    generator: Literal["llm", "extractive"]
    sentinels: list[str] = field(default_factory=list)
    raw_model_output: str | None = None


@dataclass(frozen=True)
class Answer:
    """The final, validated, user-visible response."""

    text: str
    kind: Literal[
        "factual",
        "not_in_corpus",
        "refusal",
        "performance_redirect",
        "out_of_corpus",
        "pii_refusal",
        "smalltalk",
    ]
    citation_url: str | None
    citation_source_id: str | None
    last_updated: str | None
    generator: str
    retrieved: list[ScoredChunk]
    trace: dict


@dataclass(frozen=True)
class GateResult:
    """Outcome of the grounding gate (architecture.md §11.6)."""

    passed: bool
    top_score: float
    threshold: float
    reason: str
    covered_terms: list[str]


@dataclass(frozen=True)
class BuildReport:
    """Summary of one offline index build, persisted to data/build_report.json."""

    sources_ok: list[str]
    sources_failed: list[tuple[str, str]]
    chunk_count: int
    chunk_stats: dict
    warnings: list[str]
    duration_s: float
    config_hash: str
    corpus_hash: str
