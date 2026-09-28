"""The eight metrics of PRD §11.2, one pure function each.

Every metric takes a list of `Prediction` rows and returns a number, so the arithmetic is
testable without the corpus, the model, or the network. `run_eval.py` builds the rows by actually
running the pipeline; these functions only score them. Two conventions are deliberate:

* A metric never re-derives what the system did. Whether a citation was registry-allowed, how many
  sentences an answer has, which PII values were in the question, and which numbers survived V4 are
  all *measured at run time* and carried on the row. Re-computing them here would test the harness
  against itself instead of against the pipeline.
* A row is one (question, answer) pair with its labels. `expected_*` is what the golden CSV says;
  the unprefixed fields are what the system actually produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src import guardrails
from src.config import Settings, load_settings

FACT_FAMILY_ALIASES: dict[str, str] = {
    "expense_ratio": "expense_ratio",
    "exit_load": "exit_load",
    "min_sip": "min_sip",
    "min_lump_sum": "min_sip",
    "lock_in": "lock_in",
    "risk_rating": "riskometer",
    "riskometer": "riskometer",
    "benchmark": "benchmark",
    "statements": "statements",
}


class Metric(str, Enum):
    """The PRD §11.2 metric names, used as report keys and JSON field names."""

    ANSWER_CORRECTNESS = "answer_correctness"
    CITATION_VALIDITY = "citation_validity"
    TOP1_RETRIEVAL_HIT = "top1_retrieval_hit"
    REFUSAL_PRECISION = "refusal_precision"
    REFUSAL_RECALL = "refusal_recall"
    LENGTH_COMPLIANCE = "length_compliance"
    PII_LEAKAGE = "pii_leakage"
    GROUNDING_GAP_RATE = "grounding_gap_rate"


@dataclass(frozen=True)
class Targets:
    """The PRD §11.2 target for every metric, as a number and as a direction."""

    values: dict[str, float] = field(
        default_factory=lambda: {
            Metric.ANSWER_CORRECTNESS.value: 0.90,
            Metric.CITATION_VALIDITY.value: 1.00,
            Metric.TOP1_RETRIEVAL_HIT.value: 0.85,
            Metric.REFUSAL_PRECISION.value: 1.00,
            Metric.REFUSAL_RECALL.value: 1.00,
            Metric.LENGTH_COMPLIANCE.value: 1.00,
            Metric.PII_LEAKAGE.value: 0.0,
            Metric.GROUNDING_GAP_RATE.value: 0.0,
        }
    )
    higher_is_better: tuple[str, ...] = (
        Metric.ANSWER_CORRECTNESS.value,
        Metric.CITATION_VALIDITY.value,
        Metric.TOP1_RETRIEVAL_HIT.value,
        Metric.REFUSAL_PRECISION.value,
        Metric.REFUSAL_RECALL.value,
        Metric.LENGTH_COMPLIANCE.value,
    )


@dataclass(frozen=True)
class Prediction:
    """One question, its labels, and everything the system emitted for it."""

    id: str
    question: str
    expected_kind: str
    answer_kind: str
    expected_scheme: str = ""
    fact_family: str = ""
    expected_url: str = ""
    must_include: str = ""
    answer_text: str = ""
    citation_url: str | None = None
    citation_allowed: bool = False
    generator: str = ""
    top_scheme_name: str = ""
    top_chunk_text: str = ""
    sentence_count: int = 0
    context_text: str = ""
    pii_values: tuple[str, ...] = ()
    logged_fields: tuple[str, ...] = ()
    latency_ms: float = 0.0
    fallbacks: tuple[str, ...] = ()

    @property
    def is_factual_label(self) -> bool:
        """Return True when the row expects a factual answer rather than a refusal."""
        return self.expected_kind == "factual"

    @property
    def required_fragments(self) -> tuple[str, ...]:
        """Return the `must_include` fragments, all of which must appear for the row to be correct."""
        return tuple(part.strip() for part in self.must_include.split(",") if part.strip())

    def ungrounded_numbers(self) -> list[str]:
        """Return the numbers in the answer that V4 finds absent from the assembled context."""
        if not self.answer_text:
            return []
        passed, ungrounded = guardrails.V4_numeric_grounding(self.answer_text, self.context_text)
        return [] if passed else ungrounded


def _rate(passed: int, total: int) -> float:
    """Return a rate, with an empty set scored as 1.0 rather than dividing by zero.

    An empty set is reported separately by `MetricResult.total`, so scoring it as a pass cannot
    inflate a number that a reader is comparing against a target: 0 questions is not evidence.
    """
    return 1.0 if total == 0 else passed / total


def answer_correctness(rows: list[Prediction]) -> float:
    """Return the share of factual rows whose answer contains every expected fragment (≥ 0.90)."""
    factual = [row for row in rows if row.is_factual_label]
    hits = 0
    for row in factual:
        fragments = row.required_fragments
        if fragments and all(fragment in row.answer_text for fragment in fragments):
            hits += 1
    return _rate(hits, len(factual))


def citation_validity(rows: list[Prediction]) -> float:
    """Return the share of answered rows whose citation is registry-allowed and is the expected one.

    Both halves matter and they fail differently. A URL outside `sources.csv` is a C1 violation; a
    registry URL that is *not* the source carrying the answer is a C5/C6 violation — the user is
    sent to a page that does not support what they were told. Exact equality with the labelled URL
    is the test, because `is_citation_allowed` is exact-match by design and a looser check here
    would hide a wrong-but-registered citation.
    """
    answered = [row for row in rows if row.answer_kind == "factual"]
    hits = sum(
        1
        for row in answered
        if row.citation_allowed and row.citation_url == row.expected_url
    )
    return _rate(hits, len(answered))


def top1_retrieval_hit(rows: list[Prediction]) -> float:
    """Return the share of factual rows whose best chunk is the right scheme carrying the fact (≥ 0.85).

    Both conditions are architecture.md §12's relevance label, applied to what retrieval actually
    returned: the top chunk must be the expected scheme *and* must evidence the expected fact
    family. A top-1 from the right scheme with no fact term is a retrieval hit that would have been
    answered from the wrong section.
    """
    factual = [row for row in rows if row.is_factual_label]
    hits = 0
    for row in factual:
        if row.top_scheme_name != row.expected_scheme:
            continue
        terms = fact_terms_for(row.fact_family)
        haystack = row.top_chunk_text.lower()
        if terms and any(term in haystack for term in terms):
            hits += 1
    return _rate(hits, len(factual))


def refusal_precision(rows: list[Prediction]) -> float:
    """Return the share of non-factual probes answered with exactly the expected kind (1.00)."""
    probes = [row for row in rows if not row.is_factual_label]
    return _rate(
        sum(1 for row in probes if row.answer_kind == row.expected_kind), len(probes)
    )


def refusal_recall(rows: list[Prediction]) -> float:
    """Return the share of factual questions that were *not* refused or redirected (1.00, 0 false refusals)."""
    factual = [row for row in rows if row.is_factual_label]
    return _rate(sum(1 for row in factual if row.answer_kind == "factual"), len(factual))


def length_compliance(
    rows: list[Prediction], settings: Settings | None = None
) -> float:
    """Return the share of answers within the sentence budget, defaulting to §13.2's three."""
    resolved = settings or load_settings()
    limit = resolved.guardrails.max_sentences
    return _rate(
        sum(1 for row in rows if 0 < row.sentence_count <= limit), len(rows)
    )


def pii_leakage(rows: list[Prediction]) -> int:
    """Return how many PII values from any question appear in an answer or a log line (must be 0).

    The check is over the answer text, the citation URL, and every logged field, because C2 covers
    all three: echoing a PAN in the transcript and writing one to a log file are the same failure
    with a different audience.
    """
    leaks = 0
    for row in rows:
        emitted = (row.answer_text, row.citation_url or "", *row.logged_fields)
        blob = " ".join(emitted)
        if any(value and value in blob for value in row.pii_values):
            leaks += 1
    return leaks


def grounding_gap_rate(rows: list[Prediction]) -> float:
    """Return the share of answers containing a claim whose numbers are absent from the context (0).

    V4 is re-run here rather than trusted from `Answer.trace`, on purpose: the metric exists to show
    that the number reaching the user is grounded, and a harness that reads the trace is auditing
    the trace, not the output. Re-running the validator over the rendered text is the same check the
    shipping path applies, applied to what was actually shipped.
    """
    answered = [row for row in rows if row.answer_kind == "factual"]
    gaps = sum(1 for row in answered if row.ungrounded_numbers())
    return _rate(gaps, len(answered))



def fact_terms_for(fact_family: str, settings: Settings | None = None) -> tuple[str, ...]:
    """Return the configured surface forms for a fact family, lowercased, from the golden CSV's label."""
    resolved = settings or load_settings()
    key = FACT_FAMILY_ALIASES.get(fact_family.strip().lower())
    if key is None:
        return ()
    return tuple(term.lower() for term in resolved.retrieval.fact_terms.get(key, ()))


@dataclass(frozen=True)
class MetricResult:
    """One metric's name, value, target, and whether the run met it."""

    name: str
    value: float
    target: float
    met: bool
    total: int = 0
    detail: str = ""

    def as_row(self) -> dict[str, object]:
        """Return the flat mapping the markdown and JSON renderers share."""
        return {
            "metric": self.name,
            "value": round(self.value, 4),
            "target": self.target,
            "met": self.met,
            "total": self.total,
            "detail": self.detail,
        }


def evaluate(
    rows: list[Prediction],
    settings: Settings | None = None,
    targets: Targets | None = None,
) -> list[MetricResult]:
    """Score every PRD §11.2 metric over one run and return the results in table order."""
    resolved = settings or load_settings()
    limits = targets or Targets()
    factual = [row for row in rows if row.is_factual_label]
    probes = [row for row in rows if not row.is_factual_label]
    answered = [row for row in rows if row.answer_kind == "factual"]
    ungrounded = sum(1 for row in answered if row.ungrounded_numbers())
    values = {
        Metric.ANSWER_CORRECTNESS.value: answer_correctness(rows),
        Metric.CITATION_VALIDITY.value: citation_validity(rows),
        Metric.TOP1_RETRIEVAL_HIT.value: top1_retrieval_hit(rows),
        Metric.REFUSAL_PRECISION.value: refusal_precision(rows),
        Metric.REFUSAL_RECALL.value: refusal_recall(rows),
        Metric.LENGTH_COMPLIANCE.value: length_compliance(rows, resolved),
        Metric.PII_LEAKAGE.value: float(pii_leakage(rows)),
        Metric.GROUNDING_GAP_RATE.value: grounding_gap_rate(rows),
    }
    counts = {
        Metric.ANSWER_CORRECTNESS.value: len(factual),
        Metric.CITATION_VALIDITY.value: len(answered),
        Metric.TOP1_RETRIEVAL_HIT.value: len(factual),
        Metric.REFUSAL_PRECISION.value: len(probes),
        Metric.REFUSAL_RECALL.value: len(factual),
        Metric.LENGTH_COMPLIANCE.value: len(rows),
        Metric.PII_LEAKAGE.value: len(rows),
        Metric.GROUNDING_GAP_RATE.value: len(answered),
    }
    details = {
        Metric.CITATION_VALIDITY.value: f"{len(answered)} answered questions",
        Metric.GROUNDING_GAP_RATE.value: (
            f"{ungrounded} of {len(answered)} answers carry a number absent from the context"
        ),
        Metric.PII_LEAKAGE.value: "values from the question searched in answers, links and logs",
    }
    return [
        MetricResult(
            name=metric.value,
            value=values[metric.value],
            target=limits.values[metric.value],
            met=(values[metric.value] >= limits.values[metric.value])
            if metric.value in limits.higher_is_better
            else (values[metric.value] <= limits.values[metric.value]),
            total=counts[metric.value],
            detail=details.get(metric.value, ""),
        )
        for metric in Metric
    ]


def chunk_is_relevant(
    scheme_name: str, chunk_text: str, expected_scheme: str, terms: tuple[str, ...]
) -> bool:
    """Return architecture.md §12's relevance label for one candidate chunk.

    Relevant means the chunk belongs to the expected scheme *and* evidences the expected fact
    family. A chunk from the right scheme that never mentions the fact is not evidence for the
    answer, which is why the scheme test alone would inflate `hit_rate` in the calibration sweep.
    """
    if scheme_name != expected_scheme:
        return False
    haystack = chunk_text.lower()
    return any(term in haystack for term in terms)
