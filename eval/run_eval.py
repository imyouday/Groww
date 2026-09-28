"""The eval harness: PRD §11.2 metrics, the §12 threshold calibration, and ablations A1-A4.

Three jobs, deliberately separated so a number in `eval/report.md` is traceable to the code that
produced it:

* `--mode metrics` answers "how good is the shipped configuration?" over `eval/golden_questions.csv`
  and `eval/out_of_scope_probes.csv`, by running the real pipeline and scoring the real output.
* `--mode calibration` implements architecture.md §12 as code and returns a τ, not a preference.
* `--mode ablation` answers "does each stage earn its place?" by re-running the same golden set with
  one stage changed at a time.

Every run appends a dated section to `eval/report.md`. Nothing here invents a value: if a stage
cannot produce a number, the harness says so rather than printing a placeholder.

Rebuilds (ablation A1) are written to a scratch directory, never to `data/chroma`, because the demo
index must never be left holding an ablation's chunks. A1 is the only mode that writes a store.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.checks import (  # noqa: E402
    Metric,
    MetricResult,
    Prediction,
    Targets,
    chunk_is_relevant,
    evaluate,
    fact_terms_for,
)
from src import chunking, guardrails, pipeline, store  # noqa: E402
from src.config import ChunkingStrategy, Settings, config_hash, load_settings  # noqa: E402
from src.models import Answer, AssembledContext, ScoredChunk  # noqa: E402
from src.pii import detect  # noqa: E402
from src.registry import load_registry  # noqa: E402
from src.retrieval import _run  # noqa: E402

GOLDEN_CSV = REPO_ROOT / "eval" / "golden_questions.csv"
PROBES_CSV = REPO_ROOT / "eval" / "out_of_scope_probes.csv"
REPORT_MD = REPO_ROOT / "eval" / "report.md"

A1_VARIANTS = ("semantic_600", "semantic_350", "fixed_512")
A2_THRESHOLDS = (0.25, 0.35, 0.45)
TAU_SWEEP = tuple(round(0.20 + 0.05 * step, 2) for step in range(9))


@dataclass(frozen=True)
class GoldenRow:
    """One row of `eval/golden_questions.csv`."""

    id: str
    question: str
    expected_scheme: str
    fact_family: str
    expected_url: str
    must_include: str


@dataclass(frozen=True)
class ProbeRow:
    """One row of `eval/out_of_scope_probes.csv`."""

    id: str
    query: str
    expected_kind: str
    expected_link_type: str


@dataclass(frozen=True)
class LabelledScore:
    """Architecture.md §12's per-question pair: the best relevant score and the best irrelevant one."""

    id: str
    relevant: float | None
    irrelevant: float | None


@dataclass(frozen=True)
class SweepPoint:
    """One τ in the calibration sweep, with both rates the procedure asks for."""

    tau: float
    hit_rate: float
    false_gate: float

    def meets(self, hit_rate_target: float = 0.85) -> bool:
        """Return True when this τ is admissible: hit rate at target and no false gate."""
        return self.hit_rate >= hit_rate_target and self.false_gate == 0.0


@dataclass(frozen=True)
class Calibration:
    """The calibration result: the chosen τ, the sweep it came from, and the labelled scores."""

    tau: float | None
    sweep: tuple[SweepPoint, ...]
    scores: tuple[LabelledScore, ...]
    hit_rate_target: float

    @property
    def separating_band(self) -> tuple[float, float] | None:
        """Return `(low, high)`, the open-closed interval of τ that separates every labelled score.

        `low` is the worst irrelevant score and `high` the best relevant one, so a τ in `(low, high]`
        keeps all 24 hits and drops all 24 false gates. `None` when the two overlap, which means no
        threshold exists at any value — the §12 "fix the corpus" case. Reported because a sweep that
        finds nothing inside the mandated grid has to say *where* the separation actually is, or
        "no admissible τ" reads as a broken procedure rather than as a measured result.
        """
        relevant = [score.relevant for score in self.scores if score.relevant is not None]
        irrelevant = [score.irrelevant for score in self.scores if score.irrelevant is not None]
        if not relevant or not irrelevant:
            return None
        low, high = max(irrelevant), min(relevant)
        return (round(low, 4), round(high, 4)) if low < high else None

    @property
    def band_in_sweep(self) -> bool:
        """Return True when the separating band, if any, overlaps the swept grid of τ values."""
        band = self.separating_band
        if band is None:
            return False
        return any(band[0] < point.tau <= band[1] for point in self.sweep)

    def as_dict(self) -> dict[str, Any]:
        """Return the flat mapping the markdown and JSON renderers share."""
        return {
            "tau": self.tau,
            "hit_rate_target": self.hit_rate_target,
            "separating_band": self.separating_band,
            "band_in_sweep": self.band_in_sweep,
            "sweep": [
                {
                    "tau": point.tau,
                    "hit_rate": round(point.hit_rate, 4),
                    "false_gate": round(point.false_gate, 4),
                }
                for point in self.sweep
            ],
            "scores": [
                {"id": score.id, "s_i": score.relevant, "t_i": score.irrelevant}
                for score in self.scores
            ],
        }


def read_csv(path: Path) -> list[dict[str, str]]:
    """Return the rows of a CSV with a header, as dictionaries with whitespace stripped."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def load_golden(path: Path = GOLDEN_CSV) -> list[GoldenRow]:
    """Return the golden questions, refusing a file without the header the plan fixed."""
    rows = read_csv(path)
    required = {
        "id",
        "question",
        "expected_scheme",
        "fact_family",
        "expected_url",
        "must_include",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path} must have the header {sorted(required)}")
    return [GoldenRow(**row) for row in rows]


def load_probes(path: Path = PROBES_CSV) -> list[ProbeRow]:
    """Return the out-of-scope probes, refusing a file without the header the plan fixed."""
    rows = read_csv(path)
    required = {"id", "query", "expected_kind", "expected_link_type"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path} must have the header {sorted(required)}")
    return [ProbeRow(**row) for row in rows]


class _ListHandler(logging.Handler):
    """A handler that keeps formatted records in a list instead of writing them anywhere."""

    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        """Append the record's message to the sink."""
        self._sink.append(record.getMessage())


class LogCapture:
    """Collect the pipeline logger's lines so PII leakage is measured rather than assumed.

    PRD C2 covers the log as well as the transcript, and the logging policy (§14.3) is enforced by
    an allowlist in `src/pipeline.py`. A metric that could not see the log would report 0 leakage
    for a system that had never been asked the question, which is the one way this number lies.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._handler = _ListHandler(self.lines)
        self._logger = pipeline.configure_logging(log_queries=True)
        self._saved: list[logging.Handler] = list(self._logger.handlers)

    def __enter__(self) -> "LogCapture":
        """Return this capture, attached to the pipeline logger as its only handler.

        The console handler is detached for the duration: 32 questions logged verbatim would bury the
        table this harness exists to print, and the same lines are being kept in `lines` anyway.
        """
        self._logger.handlers = [self._handler]
        self._logger.propagate = False
        return self

    def __exit__(self, *_exc: object) -> None:
        """Restore the console handler and detach, so a later run is not writing into a stale sink."""
        self._logger.handlers = self._saved

    def mark(self) -> int:
        """Return the current position, for attributing later lines to one question."""
        return len(self.lines)

    def since(self, mark: int) -> tuple[str, ...]:
        """Return the lines emitted after a mark taken in this same capture."""
        return tuple(self.lines[mark:])


def pii_values_in(text: str) -> tuple[str, ...]:
    """Return the personal values a question carries, for the leakage check to search for.

    `detect` never returns the matched text by design (§14.1), so the spans are re-read here from
    the question itself. The question is the only place these strings exist, they never reach disk,
    and this tuple lives in memory for the length of one eval row.
    """
    values: list[str] = []
    for hit in detect(text):
        value = text[hit.start : hit.end].strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _context_of(result: Answer) -> str:
    """Return the corpus text the answer was drawn from, as the retrieved chunks read."""
    return "\n".join(item.chunk.text for item in result.retrieved)


def _fallbacks(trace: dict[str, Any]) -> tuple[str, ...]:
    """Return the degradation names a trace recorded, so a fallback is visible in the report.

    Only actual degradations count. `guardrail` is written on every answered question, with `passed`
    on the healthy path, so keying on its presence would report a fallback for all 24 rows of a run
    where nothing fell back — a column that is always full teaches a reviewer to ignore it.
    """
    names: list[str] = []
    verdict = trace.get("guardrail")
    if isinstance(verdict, str) and verdict and verdict != "passed":
        names.append(f"guardrail={verdict}")
    timings = trace.get("timings_ms")
    if isinstance(timings, dict):
        for key in ("generator_error", "extractive_retry_ms"):
            if key in timings:
                names.append(f"{key}={timings[key]}")
    for key in ("fallback", "generator_failed"):
        value = trace.get(key)
        if isinstance(value, str) and value:
            names.append(f"{key}={value}")
    return tuple(names)


def _predict(
    row_id: str,
    question: str,
    expected_kind: str,
    settings: Settings,
    provider: str | None,
    logs: LogCapture,
    expected_scheme: str = "",
    fact_family: str = "",
    expected_url: str = "",
    must_include: str = "",
) -> Prediction:
    """Run one question through the real pipeline and record everything the metrics need."""
    registry = load_registry(settings)
    mark = logs.mark()
    started = time.perf_counter()
    result = pipeline.answer(question, provider, settings)
    elapsed = (time.perf_counter() - started) * 1000
    top = result.retrieved[0] if result.retrieved else None
    return Prediction(
        id=row_id,
        question=question,
        expected_kind=expected_kind,
        answer_kind=result.kind,
        expected_scheme=expected_scheme,
        fact_family=fact_family,
        expected_url=expected_url,
        must_include=must_include,
        answer_text=result.text,
        citation_url=result.citation_url,
        citation_allowed=bool(
            result.citation_url and registry.is_citation_allowed(result.citation_url)
        ),
        generator=result.generator,
        top_scheme_name=top.chunk.scheme_name if top else "",
        top_chunk_text=top.chunk.text if top else "",
        sentence_count=len(guardrails.split_sentences(result.text)),
        context_text=_context_of(result),
        pii_values=pii_values_in(question),
        logged_fields=logs.since(mark),
        latency_ms=elapsed,
        fallbacks=_fallbacks(result.trace),
    )


def run_metrics(
    settings: Settings | None = None,
    provider: str | None = None,
    golden: Sequence[GoldenRow] | None = None,
    probes: Sequence[ProbeRow] | None = None,
) -> tuple[list[Prediction], list[MetricResult]]:
    """Answer every golden question and every probe, then score the eight PRD §11.2 metrics."""
    resolved = settings or load_settings()
    golden_rows = list(golden if golden is not None else load_golden())
    probe_rows = list(probes if probes is not None else load_probes())
    predictions: list[Prediction] = []
    with LogCapture() as logs:
        for row in golden_rows:
            predictions.append(
                _predict(
                    row.id,
                    row.question,
                    "factual",
                    resolved,
                    provider,
                    logs,
                    expected_scheme=row.expected_scheme,
                    fact_family=row.fact_family,
                    expected_url=row.expected_url,
                    must_include=row.must_include,
                )
            )
        for probe in probe_rows:
            predictions.append(
                _predict(
                    probe.id, probe.query, probe.expected_kind, resolved, provider, logs
                )
            )
    return predictions, evaluate(predictions, resolved, Targets())


def _read_chunks(settings: Settings) -> list[dict[str, Any]]:
    """Return the persisted chunk dump as dictionaries."""
    path = settings.paths.resolve("chunks_dump")
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _chunk_meta(settings: Settings, chunk_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Return `chunk_id -> {"text", "scheme_name"}` for the ids a trace names, from the chunk dump.

    The scheme name is read from the dump rather than from the trace: a trace candidate carries the
    scheme *id* (`S5`), while `chunk_is_relevant` has to compare against the full scheme name in the
    golden set, and `S5` never equals `Parag Parag Flexi Cap`.
    """
    wanted = set(chunk_ids)
    return {
        str(chunk["chunk_id"]): {
            "text": str(chunk["text"]),
            "scheme_name": str(chunk["scheme_name"]),
        }
        for chunk in _read_chunks(settings)
        if chunk["chunk_id"] in wanted
    }


def labelled_scores(
    settings: Settings | None = None, golden: Sequence[GoldenRow] | None = None
) -> tuple[LabelledScore, ...]:
    """Return §12's per-question `s_i` and `t_i`, measured with the gate disabled.

    Retrieval runs at τ = 0 so every candidate survives, each candidate is labelled by
    `chunk_is_relevant`, `s_i` is the best relevant score and `t_i` the best irrelevant one. A
    question with no relevant chunk has `s_i = None` and can never count as a hit, which is the
    honest reading: the corpus does not contain that answer.
    """
    resolved = settings or load_settings()
    disabled = replace(resolved, retrieval=replace(resolved.retrieval, gate_threshold=0.0))
    scores: list[LabelledScore] = []
    for row in golden if golden is not None else load_golden():
        terms = fact_terms_for(row.fact_family, resolved)
        _context, _gate, trace = _run(row.question, disabled)
        candidates: list[dict[str, Any]] = list(trace["candidates"])
        meta = _chunk_meta(disabled, [str(item["chunk_id"]) for item in candidates])
        relevant: list[float] = []
        irrelevant: list[float] = []
        for candidate in candidates:
            score = float(candidate["final"])
            chunk_id = str(candidate["chunk_id"])
            found = meta.get(chunk_id, {"text": "", "scheme_name": ""})
            if chunk_is_relevant(
                found["scheme_name"], found["text"], row.expected_scheme, terms
            ):
                relevant.append(score)
            else:
                irrelevant.append(score)
        scores.append(
            LabelledScore(
                id=row.id,
                relevant=max(relevant) if relevant else None,
                irrelevant=max(irrelevant) if irrelevant else None,
            )
        )
    return tuple(scores)


def sweep_threshold(
    scores: Sequence[LabelledScore], hit_rate_target: float = 0.85
) -> tuple[SweepPoint, ...]:
    """Return `hit_rate` and `false_gate` for every τ in §12's sweep, in ascending order."""
    total = len(scores)
    points: list[SweepPoint] = []
    for tau in TAU_SWEEP:
        hits = sum(1 for score in scores if score.relevant is not None and score.relevant >= tau)
        false_gates = sum(
            1 for score in scores if score.irrelevant is not None and score.irrelevant >= tau
        )
        points.append(
            SweepPoint(
                tau=tau,
                hit_rate=hits / total if total else 1.0,
                false_gate=false_gates / total if total else 0.0,
            )
        )
    return tuple(points)


def calibrate_threshold(
    settings: Settings | None = None,
    golden: Sequence[GoldenRow] | None = None,
    hit_rate_target: float = 0.85,
) -> Calibration:
    """Return the smallest τ with `hit_rate ≥ 0.85` and `false_gate == 0`, per architecture.md §12.

    Smallest, not largest: a higher τ also passes the hit-rate test and only refuses more, so taking
    the largest admissible value would buy false refusals for no measured gain. When no τ is
    admissible the result is `None`, because the procedure is explicit that the fix is then the
    corpus or the chunk boundaries and never a relaxed threshold.
    """
    scores = labelled_scores(settings, golden)
    sweep = sweep_threshold(scores, hit_rate_target)
    chosen = next((point.tau for point in sweep if point.meets(hit_rate_target)), None)
    return Calibration(
        tau=chosen, sweep=sweep, scores=scores, hit_rate_target=hit_rate_target
    )


def _scratch_settings(base: Settings, name: str, scratch: Path) -> Settings:
    """Return settings that build into a scratch directory named after the run.

    The scratch paths are the whole point: ablation A1 rebuilds the index three times, and writing
    those chunks into `data/chroma` would leave the demo answering from an ablation's store. The
    chunk dump and the build report move with it for the same reason.
    """
    return replace(
        base,
        paths=replace(
            base.paths,
            chroma_dir=str(scratch / f"chroma-{name}"),
            chunks_dump=str(scratch / f"chunks-{name}.jsonl"),
        ),
    )


def _metric(results: Sequence[MetricResult], metric: Metric) -> float:
    """Return one metric's value from an `evaluate` result."""
    for result in results:
        if result.name == metric.value:
            return result.value
    raise KeyError(metric.value)


def _retrieved(settings: Settings, question: str) -> list[ScoredChunk]:
    """Return the chunks retrieval selected for a question, or none when the gate closed."""
    context, _gate, _trace = _run(question, settings)
    return list(context.chunks) if isinstance(context, AssembledContext) else []


def ablation_a1(
    settings: Settings | None = None,
    provider: str | None = None,
    scratch: Path | None = None,
    variants: Sequence[str] = A1_VARIANTS,
) -> list[dict[str, Any]]:
    """Compare chunking strategies by rebuilding the index for each and re-running the golden set.

    Each variant is built into its own scratch store and answered against *that* store, so the number
    is the number that variant would produce in production. Every store is reset before its rebuild,
    so no chunk survives from the previous variant, and the demo index is never touched.
    """
    base = settings or load_settings()
    root = scratch or (REPO_ROOT / "data" / "eval_scratch")
    root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for name in variants:
        configured = _scratch_settings(base, name, root)
        report = pipeline.build(
            rebuild=True, settings=configured, variant=chunking.Variant(name)
        )
        rows, metrics = run_metrics(configured, provider)
        tokens = [int(chunk["token_count"]) for chunk in _read_chunks(configured)]
        latencies = [row.latency_ms for row in rows]
        results.append(
            {
                "variant": name,
                "chunks": report.chunk_count,
                "median_tokens": round(statistics.median(tokens), 1) if tokens else 0.0,
                "max_tokens": max(tokens) if tokens else 0,
                "answer_correctness": round(_metric(metrics, Metric.ANSWER_CORRECTNESS), 4),
                "citation_validity": round(_metric(metrics, Metric.CITATION_VALIDITY), 4),
                "top1_retrieval_hit": round(_metric(metrics, Metric.TOP1_RETRIEVAL_HIT), 4),
                "refusal_recall": round(_metric(metrics, Metric.REFUSAL_RECALL), 4),
                "median_latency_ms": round(statistics.median(latencies), 1),
            }
        )
    return results


def ablation_a2(
    settings: Settings | None = None, provider: str | None = None
) -> dict[str, Any]:
    """Return the calibration sweep plus the golden set re-run at the three PRD §11.3 thresholds."""
    base = settings or load_settings()
    calibration = calibrate_threshold(base)
    table: list[dict[str, Any]] = []
    for tau in A2_THRESHOLDS:
        configured = replace(base, retrieval=replace(base.retrieval, gate_threshold=tau))
        rows, metrics = run_metrics(configured, provider)
        table.append(
            {
                "tau": tau,
                "answer_correctness": round(_metric(metrics, Metric.ANSWER_CORRECTNESS), 4),
                "refusal_recall": round(_metric(metrics, Metric.REFUSAL_RECALL), 4),
                "citation_validity": round(_metric(metrics, Metric.CITATION_VALIDITY), 4),
                "answered": sum(1 for row in rows if row.answer_kind == "factual"),
                "is_calibrated": tau == calibration.tau,
            }
        )
    return {"calibration": calibration.as_dict(), "thresholds": table}


def _no_boosts(base: Settings) -> Settings:
    """Return settings with every boost weight at zero, which is dense-only ranking."""
    boosts = base.retrieval.boosts
    return replace(
        base,
        retrieval=replace(
            base.retrieval,
            boosts=replace(
                boosts,
                fact_term=0.0,
                additional_fact_term=0.0,
                section_type_match=0.0,
                scheme_match=0.0,
            ),
        ),
    )


def _mmr_off(base: Settings) -> Settings:
    """Return settings with MMR switched off via λ = 1.0, which is pure relevance ordering.

    Applied to a settings object rather than fused into the row construction, so that the boost
    zeroing done by `_no_boosts` survives: replacing the whole `retrieval` block with one built
    from `base` would silently restore the boosts and make `dense_only` a duplicate of
    `dense_boost`.
    """
    return replace(base, retrieval=replace(base.retrieval, mmr_lambda=1.0))


def ablation_a3(
    settings: Settings | None = None, provider: str | None = None
) -> list[dict[str, Any]]:
    """Compare dense-only, dense + keyword boost, and dense + boost + MMR.

    MMR is switched off by λ = 1.0, which reduces its selection to pure relevance ordering, so the
    three rows differ by exactly one mechanism each. `mean_schemes_in_context` is the column that
    shows what MMR actually buys: a context that is five chunks of one scheme cannot be checked
    against a second source, and top-1 accuracy alone would not reveal it.
    """
    base = settings or load_settings()
    configurations: list[tuple[str, Settings]] = [
        ("dense_only", _mmr_off(_no_boosts(base))),
        ("dense_boost", _mmr_off(base)),
        ("dense_boost_mmr", base),
    ]
    results: list[dict[str, Any]] = []
    for label, configured in configurations:
        rows, metrics = run_metrics(configured, provider)
        factual = [row for row in rows if row.is_factual_label]
        selections = [_retrieved(configured, row.question) for row in factual]
        results.append(
            {
                "configuration": label,
                "top1_retrieval_hit": round(_metric(metrics, Metric.TOP1_RETRIEVAL_HIT), 4),
                "answer_correctness": round(_metric(metrics, Metric.ANSWER_CORRECTNESS), 4),
                "citation_validity": round(_metric(metrics, Metric.CITATION_VALIDITY), 4),
                "mean_schemes_in_context": round(
                    statistics.mean(
                        [len({item.chunk.scheme_id for item in chosen}) for chosen in selections]
                    ),
                    2,
                )
                if selections
                else 0.0,
                "mean_context_tokens": round(
                    statistics.mean(
                        [sum(item.chunk.token_count for item in chosen) for chosen in selections]
                    ),
                    1,
                )
                if selections
                else 0.0,
            }
        )
    return results


def ablation_a4(
    settings: Settings | None = None, providers: Sequence[str] = ("extractive", "llm")
) -> list[dict[str, Any]]:
    """Compare the LLM generator against the extractive composer on the same golden set.

    The point of the row is not that the LLM writes worse prose. It is that the extractive path has
    no failure mode the LLM has: every number it emits is lifted from a retrieved chunk, so its
    grounding gap is zero by construction rather than by validation. `fallbacks` counts the drafts
    the validators rejected, which is the number that says how much of the LLM's output was usable.
    """
    base = settings or load_settings()
    results: list[dict[str, Any]] = []
    for provider in providers:
        rows, metrics = run_metrics(base, provider)
        factual = [row for row in rows if row.is_factual_label]
        degraded = [row for row in factual if row.fallbacks]
        reasons: dict[str, int] = {}
        for row in degraded:
            for name in row.fallbacks:
                key = name.split("=", 1)[0]
                reasons[key] = reasons.get(key, 0) + 1
        results.append(
            {
                "provider": provider,
                "answer_correctness": round(_metric(metrics, Metric.ANSWER_CORRECTNESS), 4),
                "citation_validity": round(_metric(metrics, Metric.CITATION_VALIDITY), 4),
                "grounding_gap_rate": round(_metric(metrics, Metric.GROUNDING_GAP_RATE), 4),
                "length_compliance": round(_metric(metrics, Metric.LENGTH_COMPLIANCE), 4),
                "fallbacks": sum(len(row.fallbacks) for row in factual),
                "rows_degraded": len(degraded),
                "why": ", ".join(f"{name} x{count}" for name, count in sorted(reasons.items()))
                or "-",
                "median_latency_ms": round(
                    statistics.median([row.latency_ms for row in factual]), 1
                )
                if factual
                else 0.0,
            }
        )
    return results


def render_metrics_table(results: Sequence[MetricResult]) -> str:
    """Return the PRD §11.2 table as markdown, with a pass/fail column."""
    higher = Targets().higher_is_better
    lines = ["| Metric | Value | Target | n | Met |", "| --- | --- | --- | --- | --- |"]
    for result in results:
        comparator = ">=" if result.name in higher else "<="
        lines.append(
            f"| {result.name} | {result.value:.4g} | {comparator} {result.target:g} | "
            f"{result.total} | {'yes' if result.met else 'NO'} |"
        )
    return "\n".join(lines)


def render_rows_table(rows: Sequence[dict[str, Any]]) -> str:
    """Return any list of flat mappings as a markdown table, in insertion order."""
    if not rows:
        return "_no rows_"
    columns = list(rows[0])
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def render_calibration(calibration: Calibration) -> str:
    """Return the §12 sweep table, the chosen τ, and the labelled scores behind it."""
    sweep = render_rows_table(
        [
            {
                "tau": point.tau,
                "hit_rate": round(point.hit_rate, 4),
                "false_gate": round(point.false_gate, 4),
                "admissible": "yes" if point.meets(calibration.hit_rate_target) else "no",
            }
            for point in calibration.sweep
        ]
    )
    scores = render_rows_table(
        [
            {
                "id": score.id,
                "s_i (best relevant)": "-" if score.relevant is None else round(score.relevant, 4),
                "t_i (best irrelevant)": "-" if score.irrelevant is None else round(score.irrelevant, 4),
            }
            for score in calibration.scores
        ]
    )
    chosen = (
        f"**tau = {calibration.tau}** — the smallest admissible value."
        if calibration.tau is not None
        else "**no admissible tau** — the fix is the corpus or the chunk boundaries, not the threshold."
    )
    band = _render_band(calibration)
    return f"{chosen}\n\n{band}\n\n{sweep}\n\n{scores}"


def _render_band(calibration: Calibration) -> str:
    """Return the sentence naming where the scores actually separate, or why nothing separates.

    Named separately from the sweep table because the mandated grid is 0.20 to 0.60 and the measured
    separation on this corpus sits above it: a reader who sees only "no admissible tau" would
    reasonably assume the procedure failed, when in fact the scores are cleanly separable and the
    grid simply does not reach them.
    """
    band = calibration.separating_band
    if band is None:
        overlapping = [
            score.id
            for score in calibration.scores
            if score.relevant is not None
            and score.irrelevant is not None
            and score.irrelevant >= score.relevant
        ]
        detail = (
            f"worst overlap on {', '.join(overlapping)}: t_i >= s_i"
            if overlapping
            else "a question with no relevant chunk can never be a hit"
        )
        return f"Separating band: none — {detail}. No threshold value separates these scores."
    low, high = band
    reach = (
        "inside" if calibration.band_in_sweep else "entirely above"
    )
    return (
        f"Separating band: ({low}, {high}] — {reach} the swept grid "
        f"{{{TAU_SWEEP[0]}..{TAU_SWEEP[-1]}}}. Every s_i is at least {high} and every t_i at most "
        f"{low}, so a threshold in that interval keeps all {len(calibration.scores)} hits and drops "
        f"all {len(calibration.scores)} false gates."
    )


def failure_rows(predictions: Sequence[Prediction]) -> list[dict[str, Any]]:
    """Return one row per question that missed its label, so a failure is named, not just counted."""
    failures: list[dict[str, Any]] = []
    for row in predictions:
        reasons: list[str] = []
        if row.is_factual_label:
            fragments = row.required_fragments
            if row.answer_kind != "factual":
                reasons.append(f"answered {row.answer_kind}")
            else:
                if fragments and not all(fragment in row.answer_text for fragment in fragments):
                    missing = [item for item in fragments if item not in row.answer_text]
                    reasons.append(f"missing {missing}")
                if not row.citation_allowed:
                    reasons.append("citation not in registry")
                if row.citation_url != row.expected_url:
                    reasons.append(f"cited {row.citation_url}")
                if row.ungrounded_numbers():
                    reasons.append(f"ungrounded {row.ungrounded_numbers()}")
        elif row.answer_kind != row.expected_kind:
            reasons.append(f"expected {row.expected_kind}, got {row.answer_kind}")
        if reasons:
            failures.append(
                {
                    "id": row.id,
                    "question": row.question[:60],
                    "reason": "; ".join(reasons),
                    "answer": row.answer_text[:70],
                }
            )
    return failures


def append_report(section: str) -> None:
    """Append a dated section to `eval/report.md`, creating the file with a header if absent."""
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not REPORT_MD.is_file():
        REPORT_MD.write_text(
            "# Evaluation report\n\n"
            "Append-only. Every run adds a dated section and nothing is edited out, because the\n"
            "history of the numbers is part of the evidence.\n",
            encoding="utf-8",
        )
    with REPORT_MD.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## Run {stamp}\n\n{section}\n")


def _metrics_section(
    payload: dict[str, Any], predictions: Sequence[Prediction], metrics: Sequence[MetricResult]
) -> str:
    """Return the markdown the metrics mode prints and appends."""
    latencies = sorted(row.latency_ms for row in predictions)
    lines = [
        f"### Metrics — provider `{payload['provider']}`, "
        f"config_hash `{payload['config_hash'][:12]}`",
        "",
        render_metrics_table(metrics),
        "",
        f"{len(predictions)} rows "
        f"({sum(1 for row in predictions if row.is_factual_label)} golden, "
        f"{sum(1 for row in predictions if not row.is_factual_label)} probes). "
        f"Median latency {statistics.median(latencies):.0f} ms, p95 "
        f"{latencies[max(0, int(len(latencies) * 0.95) - 1)]:.0f} ms.",
    ]
    failures = payload["failures"]
    lines += (
        ["", "Failures:", "", render_rows_table(failures)]
        if failures
        else ["", "No row missed its label."]
    )
    return "\n".join(lines)


def _calibration_section(calibration: Calibration, settings: Settings) -> str:
    """Return the markdown the calibration mode prints and appends."""
    return "\n".join(
        [
            f"### Calibration (architecture.md §12) — configured tau "
            f"{settings.retrieval.gate_threshold}",
            "",
            render_calibration(calibration),
            "",
            "The labels are §12's own rule applied in code rather than by hand: a chunk is relevant when it",
            "is the expected scheme *and* its text carries a configured surface form of the expected fact",
            "family. That is a weaker claim than a hand-labelled set, and the same term lists also feed the",
            "retrieval boost, so this sweep measures whether the threshold separates term-bearing",
            "right-scheme chunks from the rest — not whether a human agreed. Stated rather than hidden.",
        ]
    )


def _ablation_section(
    which: str, tables: dict[str, Any], settings: Settings
) -> str:
    """Return the markdown the ablation mode prints and appends."""
    titles = {
        "A1": "A1 — chunking strategy (index rebuilt per variant into a scratch store)",
        "A2": "A2 — grounding-gate threshold",
        "A3": "A3 — dense only vs +boost vs +MMR",
        "A4": "A4 — generator: LLM vs extractive",
    }
    blocks = [f"### Ablations — {which}", ""]
    for key, value in tables.items():
        if key == "calibration":
            continue
        blocks += [titles[key], "", render_rows_table(value), ""]
    if "calibration" in tables:
        calibration = tables["calibration"]
        band = calibration.get("separating_band")
        if calibration.get("tau") is not None:
            block = f"calibrated tau {calibration['tau']}"
        elif band:
            block = (
                f"no admissible tau; separating band ({band[0]}, {band[1]}] "
                f"({'inside' if calibration.get('band_in_sweep') else 'outside'} the swept grid)"
            )
        else:
            block = "no admissible tau and no separating band"
        blocks += [
            "A2 calibration sweep (architecture.md §12)",
            "",
            block,
            "",
            render_rows_table(calibration["sweep"]),
            "",
        ]
    blocks += [
        "A1's numbers come from a scratch store per variant (`data/eval_scratch/`), reset before each",
        "rebuild, so the demo index in `data/chroma` is never left holding an ablation's chunks. A3's MMR",
        f"row is the shipped configuration (lambda = {settings.retrieval.mmr_lambda}).",
    ]
    return "\n".join(blocks).strip()


def _run_ablation(which: str, settings: Settings, provider: str | None) -> dict[str, Any]:
    """Run the requested ablation and return its tables under stable keys."""
    runners = {
        "A1": lambda: ablation_a1(settings, provider),
        "A2": lambda: ablation_a2(settings, provider),
        "A3": lambda: ablation_a3(settings, provider),
        "A4": lambda: ablation_a4(settings),
    }
    selected = list(runners) if which == "all" else [which]
    tables: dict[str, Any] = {}
    for key in selected:
        result = runners[key]()
        if key == "A2":
            tables["A2"], tables["calibration"] = result["thresholds"], result["calibration"]
        else:
            tables[key] = result
    return tables


def _apply_variant(base: Settings, name: str) -> Settings:
    """Return settings with the requested chunking variant applied in memory only."""
    bounds = chunking.Variant(name).bounds()
    return replace(
        base,
        chunking=replace(
            base.chunking,
            max_tokens=bounds[0],
            overlap_tokens=bounds[1],
            strategy=(
                ChunkingStrategy.FIXED_512
                if name == "fixed_512"
                else ChunkingStrategy.SEMANTIC_SECTION
            ),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the requested mode, print the tables, append the run to `eval/report.md`, and exit."""
    parser = argparse.ArgumentParser(
        prog="run_eval.py",
        description="Metrics, threshold calibration, and the PRD §11.3 ablations.",
    )
    parser.add_argument("--mode", choices=("metrics", "calibration", "ablation"), default="metrics")
    parser.add_argument("--ablation", choices=("A1", "A2", "A3", "A4", "all"), default="all")
    parser.add_argument(
        "--provider",
        default=None,
        help="extractive | llm | the configured default when omitted",
    )
    parser.add_argument(
        "--variant",
        choices=[item.value for item in chunking.Variant],
        help="override config.chunking in memory for this run only",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    parser.add_argument(
        "--no-append", action="store_true", help="do not write this run to eval/report.md"
    )
    args = parser.parse_args(argv)

    base = _apply_variant(load_settings(), args.variant) if args.variant else load_settings()
    provider = args.provider or pipeline.active_provider(base)
    payload: dict[str, Any] = {
        "mode": args.mode,
        "provider": provider,
        "variant": args.variant,
        "config_hash": config_hash(base),
    }

    if args.mode == "metrics":
        predictions, metrics = run_metrics(base, provider)
        payload["metrics"] = [result.as_row() for result in metrics]
        payload["failures"] = failure_rows(predictions)
        section = _metrics_section(payload, predictions, metrics)
    elif args.mode == "calibration":
        calibration = calibrate_threshold(base)
        payload["calibration"] = calibration.as_dict()
        payload["configured_tau"] = base.retrieval.gate_threshold
        section = _calibration_section(calibration, base)
    else:
        tables = _run_ablation(args.ablation, base, provider)
        payload["ablation"] = tables
        section = _ablation_section(args.ablation, tables, base)

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(section)
    if not args.no_append:
        append_report(section)
        print(f"appended to {REPORT_MD.relative_to(REPO_ROOT)}")
    rows = payload.get("metrics")
    if not rows:
        return 0
    return 0 if all(bool(row["met"]) for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
