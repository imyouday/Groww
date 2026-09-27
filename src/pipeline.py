"""The offline build (loading → chunking → embedding → storage) and the online path (intent →
retrieval → generation → guardrails), plus the CLI that runs both.

This is the only module that wires stages together (AGENTS.md). Two properties matter more than the
sequence itself. A build without `--refresh` makes no network calls at all, because
`loading.load_all` reuses the snapshots in `data/raw/` and never opens a client; and the
`corpus_hash` makes a rebuild checkable, so `--rebuild` can be asserted to produce the identical
chunk set rather than merely a similar one. On the query side, the order of the stages in `answer()`
is the guarantee that a refusal never consults the corpus or the model.

The logging policy of architecture.md §14.3 lives here too, because logging is orchestration: the
`answer()` call is the only place that knows both what was asked and what came back, and it is the
one place that can therefore record the outcome without recording the question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from src import chunking, embedding, guardrails, loading, store, templates
from src.config import Settings, config_hash, load_llm_env, load_settings
from src.generation import ExtractiveGenerator, resolve_generator
from src.intents import classify, has_pii
from src.models import Answer, BuildReport, ChunkRecord, GateResult, Intent, PipelineError
from src.registry import load_registry
from src.retrieval import NON_FACTUAL, retrieve_with_debug

VECTOR_DUMP_WIDTH = 100
VECTOR_DUMP_TEXT_WIDTH = 86
LOGGER = logging.getLogger("mf_faq")

# The only fields that may ever reach a log record (architecture.md §14.3). An allowlist rather
# than a denylist, because a denylist is only as good as the next field someone adds.
SAFE_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "stage",
        "kind",
        "intent",
        "matched_rule",
        "scheme_id",
        "scheme_name",
        "fact_family",
        "generator",
        "guardrail",
        "top_score",
        "tau",
        "chunks",
        "candidates",
        "context_tokens",
        "citation_source_id",
        "link_type",
        "pii_kinds",
        "pii_hit_count",
        "timings_ms",
        "content_hash",
        "duration_s",
        "config_hash",
        "corpus_hash",
        "chunk_count",
        "sources_ok",
        "sources_failed",
        "warnings",
    }
)


class QueryTextFilter(logging.Filter):
    """Drop any record carrying a `query` field unless LOG_QUERIES is switched on locally.

    The filter is the mechanism, not a convention: every other module is free to attach a query to
    its log record while debugging, and this is what stops that text reaching stdout by default.
    """

    def __init__(self, log_queries: bool | None = None) -> None:
        self._log_queries = (
            load_llm_env().log_queries if log_queries is None else bool(log_queries)
        )

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for a record that carries raw query text while logging is switched off."""
        if self._log_queries:
            return True
        return not hasattr(record, "query")


def configure_logging(log_queries: bool | None = None) -> logging.Logger:
    """Attach a stdout handler carrying the query-text filter, and return the pipeline logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(QueryTextFilter(log_queries))
    for existing in list(LOGGER.handlers):
        LOGGER.removeHandler(existing)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    return LOGGER


def log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Return only the allowlisted fields of a trace, for a log record.

    Everything allowlisted is a class name, an id, a count, a score, or a duration. The user query,
    the assembled context, the draft, and the raw model output are all absent by construction.
    """
    return {key: value for key, value in fields.items() if key in SAFE_LOG_FIELDS}


def log_answer(answer: Answer, logger: logging.Logger | None = None) -> None:
    """Record how a question was answered, never what it said (architecture.md §14.3)."""
    target = logger if logger is not None else LOGGER
    target.info("answered %s", json.dumps(log_fields(answer.trace), default=str))



def corpus_hash(chunks: list[ChunkRecord]) -> str:
    """Return a sha256 over every chunk's id and content, in sorted id order.

    Both the id and a hash of the embedded text are covered so that a change in either the
    chunking output or the source text moves the digest, and sorting makes it independent of the
    order the sources happened to load in.
    """
    parts: list[str] = []
    for chunk in chunks:
        digest = hashlib.sha256(chunk.embed_text.encode("utf-8")).hexdigest()
        parts.append(f"{chunk.chunk_id}:{digest}")
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def build(
    refresh: bool = False,
    rebuild: bool = False,
    settings: Settings | None = None,
) -> BuildReport:
    """Run the full offline build and return its report, having written every artefact to disk."""
    resolved = settings or load_settings()
    started = time.perf_counter()
    registry = loading.load_registry(resolved)
    docs, warnings = loading.load_all(registry, resolved, refresh=refresh)
    if not docs:
        raise PipelineError(
            "no source could be loaded; the build cannot produce an index from zero documents"
        )
    chunks, chunk_warnings = chunking.chunk_all(registry, resolved)
    if not chunks:
        raise PipelineError("loading produced documents but chunking produced no chunks")
    vectors = embedding.embed([chunk.embed_text for chunk in chunks], resolved)
    if rebuild:
        store.reset(resolved)
    written = store.upsert_chunks(chunks, vectors, resolved)
    store.delete_missing([chunk.chunk_id for chunk in chunks], resolved)
    chunking.write_chunks_jsonl(chunks, resolved.paths.resolve("chunks_dump"))
    sources_ok = [doc.source.source_id for doc in docs]
    sources_failed: list[tuple[str, str]] = []
    for warning in warnings:
        source_id, _, reason = warning.partition(": ")
        sources_failed.append((source_id, reason))
    report = BuildReport(
        sources_ok=sources_ok,
        sources_failed=sources_failed,
        chunk_count=written,
        chunk_stats=chunking.chunk_stats(chunks),
        warnings=[*warnings, *chunk_warnings],
        duration_s=round(time.perf_counter() - started, 3),
        config_hash=config_hash(resolved),
        corpus_hash=corpus_hash(chunks),
    )
    _write_report(report, resolved)
    return report


def _write_report(report: BuildReport, settings: Settings) -> Path:
    """Persist the build report next to the chunk dump, adding the fields not on the dataclass."""
    payload = asdict(report) | {
        "built_at": store.utc_now(),
        "model_id": embedding.model_id(),
        "collection": settings.chroma.collection_name,
        "space": settings.chroma.space,
        "chroma_version": store.chroma_version_string(),
    }
    path = store.report_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def summarise(report: BuildReport, settings: Settings | None = None) -> str:
    """Return the human-readable build summary the CLI prints."""
    resolved = settings or load_settings()
    stats = report.chunk_stats
    lines = [
        f"collection    {resolved.chroma.collection_name} ({resolved.chroma.space} space)",
        f"model         {embedding.model_id()}",
        f"sources ok    {len(report.sources_ok)}: {', '.join(report.sources_ok)}",
        f"sources failed {len(report.sources_failed)}",
        f"chunks        {report.chunk_count}",
        f"tokens        median {stats.get('median_tokens', 0)}, "
        f"p10 {stats.get('p10_tokens', 0)}, p90 {stats.get('p90_tokens', 0)}, "
        f"max {stats.get('max_tokens', 0)}",
        f"by type       {stats.get('by_section_type', {})}",
        f"duration      {report.duration_s:.2f} s",
        f"config_hash   {report.config_hash}",
        f"corpus_hash   {report.corpus_hash}",
    ]
    for source_id, reason in report.sources_failed:
        lines.append(f"  FAILED {source_id}: {reason}")
    for warning in report.warnings:
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


def dump(settings: Settings | None = None) -> Path:
    """Write every stored chunk with its vector to `paths.vectors_dump`, and return that path.

    The vectors are read back out of Chroma rather than recomputed, so the file is evidence of
    what the store actually holds: if a metadata field or a document failed to round-trip, the
    `document == chunk.text` assertion below fails instead of the dump quietly agreeing with
    `data/chunks.jsonl`. This is the artefact to open when a citation looks wrong.
    """
    resolved = settings or load_settings()
    collection = store.get_collection(resolved)
    stored = collection.get(include=["documents", "metadatas", "embeddings"])
    by_id = {
        chunk_id: (document, vector)
        for chunk_id, document, vector in zip(
            stored["ids"],
            stored["documents"],
            stored["embeddings"],
            strict=True,
        )
    }
    chunks = _read_chunks(resolved)
    report = _read_report(resolved)
    dimensions = len(next(iter(by_id.values()))[1])
    lines = [
        "=" * VECTOR_DUMP_WIDTH,
        "MF FAQ Assistant - chunk and embedding dump",
        "=" * VECTOR_DUMP_WIDTH,
        f"model          : {embedding.model_id()}",
        f"dimensions     : {dimensions}",
        f"space          : {resolved.chroma.space} (L2-normalised vectors, cosine == dot product)",
        f"collection     : {resolved.chroma.collection_name}",
        f"chunks         : {len(chunks)}",
        f"config_hash    : {report.get('config_hash', '')}",
        f"corpus_hash    : {report.get('corpus_hash', '')}",
        f"built_at       : {report.get('built_at', '')}",
        f"sources ok     : {', '.join(report.get('sources_ok', []))}",
        "",
        f"vector shown as {dimensions} comma-separated float32 values, in index order.",
        "A vector's norm is 1.0; the cosine similarity of two chunks is their dot product.",
        "",
    ]
    for position, chunk in enumerate(chunks, start=1):
        document, vector = by_id[chunk["chunk_id"]]
        if document != chunk["text"]:
            raise PipelineError(
                f"stored document for {chunk['chunk_id']} differs from data/chunks.jsonl; "
                "the store and the chunk dump are out of step, so rebuild the index"
            )
        values = np.asarray(vector, dtype="float32")
        lines.extend(
            [
                "-" * VECTOR_DUMP_WIDTH,
                f"CHUNK {position} of {len(chunks)}",
                "-" * VECTOR_DUMP_WIDTH,
                f"chunk_id      : {chunk['chunk_id']}",
                f"source_id     : {chunk['source_id']}",
                f"scheme        : {chunk['scheme_name']} ({chunk['scheme_id']})",
                f"section       : {chunk['section']}",
                f"section_type  : {chunk['section_type']}",
                f"tokens        : {chunk['token_count']}",
                f"ordinal       : {chunk['ordinal']}",
                f"url           : {chunk['url']}",
                f"fetched_at    : {chunk['fetched_at']}",
                *_labelled("embed_text", chunk["embed_text"]),
                *_labelled("text", chunk["text"]),
                f"vector norm   : {float(np.linalg.norm(values)):.6f}",
                f"vector[{values.size}]   : "
                f"[{', '.join(f'{value:.6f}' for value in values)}]",
                "",
            ]
        )
    path = resolved.paths.resolve("vectors_dump")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _labelled(label: str, value: str) -> list[str]:
    """Return a wrapped, labelled block of text for the vector dump."""
    body = value.splitlines() or [""]
    return [
        f"{label + ':':<14}{body[0][:VECTOR_DUMP_TEXT_WIDTH]}"
        if index == 0
        else f"{'':<14}{piece[:VECTOR_DUMP_TEXT_WIDTH]}"
        for index, piece in enumerate(body)
    ]


def _read_chunks(settings: Settings) -> list[dict[str, Any]]:
    """Return the chunk dump as parsed JSON objects, one per chunk."""
    path = settings.paths.resolve("chunks_dump")
    if not path.is_file():
        raise PipelineError(f"{path} is missing; run `python -m src.pipeline build` first")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_report(settings: Settings) -> dict[str, Any]:
    """Return the persisted build report, or an empty mapping when there is none."""
    path = store.report_path(settings)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


class _CopyValues(dict):
    """Mapping that resolves any unknown placeholder to an empty string.

    The user-visible strings in config.yaml carry `{link}` and `{scheme_count}` placeholders for
    whichever component renders them. The CLI is that component for a terminal session, and printing
    a raw `{link}` would be worse than printing nothing in its place, so an unknown key resolves to
    empty rather than raising.
    """

    def __missing__(self, key: str) -> str:
        """Return an empty string for a placeholder this renderer does not supply."""
        return ""


def _render_copy(template: str, settings: Settings, **overrides: str) -> str:
    """Fill a configured copy template with the right registry URL for that message.

    Each message names a different destination, so the caller passes it: an advice refusal links to
    investor education, a PII refusal to the help centre, and a performance redirect to the
    factsheet index. That index is deliberately empty (no official factsheet index is fetchable), so
    the rendered text degrades to a sentence without a link rather than inventing a URL, and a
    dangling ": ." left by the missing value is collapsed.
    """
    values = _CopyValues(
        link=settings.registry.education_url,
        help_link=settings.registry.help_url,
        factsheet_link=settings.registry.factsheet_index_url,
        scheme_count=len(settings.registry.scheme_aliases),
        schemes=", ".join(sorted(settings.registry.scheme_aliases)),
    )
    values.update(overrides)
    rendered = template.format_map(values)
    rendered = re.sub(r"\s*:\s*\.", ".", rendered)
    return re.sub(r"\s{2,}", " ", rendered).strip()


def answer(
    query: str,
    provider: str | None = None,
    settings: Settings | None = None,
) -> Answer:
    """Answer one question end to end, log the outcome, and return a validated, cited Answer.

    The wrapper exists so that every path is logged exactly once, including the paths that return
    before retrieval, and so that the logged fields are drawn from the finished trace rather than
    from whatever each branch happened to have in hand.
    """
    result = _answer(query, provider, settings)
    log_answer(result)
    return result


def _answer(
    query: str,
    provider: str | None = None,
    settings: Settings | None = None,
) -> Answer:
    """Run the online path for one question (architecture.md §15.2).

    The order is the guarantee. PII and the non-factual intents are answered from a template before
    anything is retrieved or generated, so a refusal cannot consult the corpus, the model, or the
    network. Only then does the grounding gate, the generator, and the validators run, with the
    deterministic composer as the single fallback when a draft fails V2 to V6.
    """
    resolved = settings if settings is not None else load_settings()
    if provider is not None:
        resolved = replace(
            resolved, generation=replace(resolved.generation, provider=provider)
        )
    registry = load_registry(resolved)
    started = time.perf_counter()
    classification = classify(query, resolved)
    pii_hits = has_pii(query)
    if pii_hits or classification.intent is Intent.PII_REQUEST:
        refusal = guardrails.route(Intent.PII_REQUEST, None, None, registry, resolved)
        return replace(
            refusal,
            trace={
                **refusal.trace,
                "pii_kinds": sorted({hit.kind.value for hit in pii_hits}),
                "pii_hit_count": len(pii_hits),
                "timings_ms": {"total_ms": round((time.perf_counter() - started) * 1000, 2)},
            },
        )
    if classification.intent in NON_FACTUAL:
        return guardrails.route(
            classification.intent,
            None,
            None,
            registry,
            resolved,
            classification.scheme_id,
        )

    context, retrieval_trace = retrieve_with_debug(query, resolved)
    timings: dict[str, float] = {
        "retrieval_ms": round((time.perf_counter() - started) * 1000, 2)
    }
    if context is None:
        gate = GateResult(
            False,
            float(retrieval_trace["gate"]["top_score"]),
            float(retrieval_trace["gate"]["threshold"]),
            str(retrieval_trace["gate"]["reason"]),
            list(retrieval_trace["gate"]["covered_terms"]),
        )
        routed = guardrails.route(
            Intent.FACTUAL_FACT, gate, None, registry, resolved, classification.scheme_id
        )
        return replace(
            routed,
            trace={**retrieval_trace, **routed.trace, "timings_ms": timings},
        )

    generator, _provider_name = resolve_generator(resolved)
    clock = time.perf_counter()
    draft = generator.generate(context, query, classification.intent)
    timings["generation_ms"] = round((time.perf_counter() - clock) * 1000, 2)
    report = guardrails.validation_report(draft, context, resolved)
    verdict = guardrails.first_failure(report)
    timings["guardrails_ms"] = round((time.perf_counter() - clock) * 1000, 2)

    if verdict.startswith("v1_"):
        intent = Intent.ADVICE_REQUEST if verdict == "v1_refusal" else Intent.FACTUAL_FACT
        routed = guardrails.route(intent, None, context, registry, resolved)
        return replace(
            routed,
            trace={**retrieval_trace, **routed.trace, "timings_ms": timings},
        )

    if verdict != "passed":
        clock = time.perf_counter()
        fallback = ExtractiveGenerator(resolved).generate(
            context, query, classification.intent
        )
        timings["extractive_retry_ms"] = round((time.perf_counter() - clock) * 1000, 2)
        fallback_report = guardrails.validation_report(fallback, context, resolved)
        fallback_verdict = guardrails.first_failure(fallback_report)
        if fallback.text and fallback_verdict == "passed":
            draft, report, verdict = fallback, fallback_report, verdict

    if verdict != "passed" or not draft.text:
        routed = guardrails.route(Intent.FACTUAL_FACT, None, context, registry, resolved)
        return replace(
            routed,
            trace={
                **retrieval_trace,
                **routed.trace,
                "guardrail": verdict,
                "validators": report,
                "timings_ms": timings,
            },
        )

    answer_value = guardrails.build_answer(
        draft,
        context,
        classification.intent,
        registry,
        resolved,
        guardrail=verdict,
        report=report,
        timings_ms=timings,
    )
    return replace(answer_value, trace={**retrieval_trace, **answer_value.trace})


def ask(query: str, provider: str | None = None, debug: bool = False) -> int:
    """Answer one question from the terminal and print the rendered answer, returning an exit code."""
    try:
        result = answer(query, provider)
    except PipelineError as exc:
        print(f"error: {exc}")
        return 2
    print(f"kind       : {result.kind}")
    print(f"generator  : {result.generator}")
    print(f"answer     : {result.text}")
    print(f"source     : {result.citation_url or '(none)'}")
    print(f"last updated: {result.last_updated or '(none)'}")
    if debug:
        print("trace      :")
        print(json.dumps(result.trace, indent=2, ensure_ascii=False, default=str))
    return 0



def main(argv: list[str] | None = None) -> int:
    """Run a pipeline command and print its summary, returning a process exit code."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description="Offline build pipeline for the MF FAQ assistant.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="build the vector index offline")
    build_parser.add_argument(
        "--refresh", action="store_true", help="re-fetch every source instead of using snapshots"
    )
    build_parser.add_argument(
        "--rebuild", action="store_true", help="delete and recreate the collection first"
    )
    subparsers.add_parser(
        "dump", help="write every stored chunk and its vector to paths.vectors_dump"
    )
    answer_parser = subparsers.add_parser(
        "ask",
        help="answer one question end to end and print the cited answer",
    )
    answer_parser.add_argument("query", nargs="?", help="the user's question")
    answer_parser.add_argument(
        "--query", dest="query_flag", default=None, help="the user's question"
    )
    answer_parser.add_argument(
        "--provider",
        choices=("auto", "llm", "extractive"),
        default=None,
        help="override config.generation.provider for this run",
    )
    answer_parser.add_argument(
        "--debug", action="store_true", help="print the full Answer.trace as JSON"
    )
    arguments = parser.parse_args(argv)
    settings = load_settings()
    configure_logging()
    if arguments.command == "dump":
        path = dump(settings)
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
        return 0
    if arguments.command == "ask":
        query_text = arguments.query_flag or arguments.query
        if not query_text:
            parser.error("ask requires a question, positionally or via --query")
        return ask(query_text, arguments.provider, arguments.debug)
    if arguments.command != "build":
        parser.error(f"unknown command {arguments.command!r}")
    report = build(refresh=arguments.refresh, rebuild=arguments.rebuild, settings=settings)
    print(summarise(report, settings))
    print(f"written {store.report_path(settings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
