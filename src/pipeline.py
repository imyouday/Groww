"""The offline build: loading → chunking → embedding → storage, plus the CLI that runs it.

This is the only module that wires stages together (AGENTS.md), and Phase 5 is the last stage it
runs: after `build()` returns, everything a question needs exists on disk, so `ask.py` in a later
phase starts with a collection it never builds.

Two properties matter more than the sequence itself. A build without `--refresh` makes no network
calls at all, because `loading.load_all` reuses the snapshots in `data/raw/` and never opens a
client; and the `corpus_hash` makes a rebuild checkable, so `--rebuild` can be asserted to produce
the identical chunk set rather than merely a similar one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from src import chunking, embedding, loading, store
from src.config import Settings, config_hash, load_settings
from src.models import BuildReport, ChunkRecord, PipelineError

VECTOR_DUMP_WIDTH = 100
VECTOR_DUMP_TEXT_WIDTH = 86


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


def main(argv: list[str] | None = None) -> int:
    """Run a pipeline command and print its summary, returning a process exit code."""
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
    arguments = parser.parse_args(argv)
    settings = load_settings()
    if arguments.command == "dump":
        path = dump(settings)
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
        return 0
    if arguments.command != "build":
        parser.error(f"unknown command {arguments.command!r}")
    report = build(refresh=arguments.refresh, rebuild=arguments.rebuild, settings=settings)
    print(summarise(report, settings))
    print(f"written {store.report_path(settings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
