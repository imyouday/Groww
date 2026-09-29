"""Stage 4: the persistent Chroma collection that retrieval queries.

Chroma is used for three reasons and not for scale: it persists to a directory with no server, it
filters by metadata inside the query (the scheme filter has to happen before ranking, not after),
and the on-disk store is something a demo can open and look at (D7). At ~100 chunks none of its
limitations matter.

Two details in this module are load-bearing rather than stylistic. Every metadata value is a
str/int because Chroma rejects `None`, and a single `None` fails the whole upsert, so the
coercion is centralised in `metadata_for()` and every caller goes through it. And `query()`
rebuilds a whole `ChunkRecord` from the document plus metadata, because a retrieval result that
has lost `fetched_at` cannot carry the C6 "last updated" stamp, and one that has lost
`token_count` cannot be budgeted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
import numpy as np
from chromadb.config import Component
from chromadb.telemetry.product import ProductTelemetryEvent

from src.config import Settings, load_settings
from src.embedding import model_id
from src.models import ChunkRecord, IndexNotBuiltError, PipelineError, ScoredChunk, SectionType

METADATA_KEYS: tuple[str, ...] = (
    "chunk_id",
    "source_id",
    "scheme_id",
    "scheme_name",
    "section",
    "section_type",
    "url",
    "title",
    "fetched_at",
    "ordinal",
    "token_count",
)
MINIMUM_CHROMA_VERSION = (0, 5)


class NoTelemetry(Component):
    """Chroma's telemetry component, replaced by one that discards every event.

    `anonymized_telemetry=False` is not enough on chromadb 0.5.23: the Posthog client is still
    constructed, and its three-positional-argument `capture()` call does not match the installed
    posthog signature, so every client creation logs an error. A demo that prints telemetry
    failures on start-up fails NFR-7 outright, so the component itself is replaced.

    This extends `Component` rather than `ProductTelemetryClient` because the latter is enforced
    by the `overrides` package, which would make this module depend on a package that is only
    chromadb's own dependency.
    """

    def capture(self, event: ProductTelemetryEvent) -> None:
        """Discard a telemetry event without sending it anywhere."""
        return None


def _client_for(path: str) -> chromadb.Client:
    """Return a persistent client for one directory, with telemetry disabled (NFR-7).

    An index directory that cannot be created is an `IndexNotBuiltError` rather than the raw
    `OSError` Chroma would raise, because the UI catches the typed error and shows the build
    instruction: a missing, blocked, or mistyped `chroma_dir` is the same problem as an index
    that was never built.
    """
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise IndexNotBuiltError(
            f"the index directory {path} is not usable ({error}); "
            f"run `python -m src.pipeline build` after fixing paths.chroma_dir"
        ) from error
    return chromadb.PersistentClient(
        path=path,
        settings=chromadb.config.Settings(
            anonymized_telemetry=False,
            chroma_product_telemetry_impl=f"{NoTelemetry.__module__}.{NoTelemetry.__qualname__}",
        ),
    )


def connect(settings: Settings | None = None) -> chromadb.Client:
    """Return the persistent client, with telemetry disabled (NFR-7)."""
    resolved = settings or load_settings()
    return _client_for(str(resolved.paths.resolve("chroma_dir")))


def chroma_version() -> tuple[int, ...]:
    """Return the installed chromadb version as a comparable tuple of ints."""
    parts: list[int] = []
    for piece in chromadb.__version__.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def collection_configuration(space: str) -> Any:
    """Return the collection's space configuration in whatever form the installed chromadb takes.

    chromadb 0.5.23 only accepts the *internal* typed configuration — passing the public
    `CollectionConfiguration` interface makes the server fail to deserialise its own JSON, and a
    plain `{"hnsw": {"space": …}}` mapping is a dict with no `to_json`. Earlier 0.5.x accepted the
    mapping and anything below 0.5 only understands `metadata={"hnsw:space": …}`. The version is
    checked and the import probed rather than a bare `except` around collection creation, which
    would also swallow a genuine path or permission error and report it as a version problem.
    """
    if chroma_version() < MINIMUM_CHROMA_VERSION:
        return None
    try:
        from chromadb.api.configuration import (
            CollectionConfigurationInternal,
            ConfigurationParameter,
            HNSWConfigurationInternal,
        )
    except ImportError:
        return {"hnsw": {"space": space}}
    hnsw = HNSWConfigurationInternal(
        parameters=[ConfigurationParameter(name="space", value=space)]
    )
    return CollectionConfigurationInternal(
        parameters=[ConfigurationParameter(name="hnsw_configuration", value=hnsw)]
    )


def _collection_for(
    client: chromadb.Client, name: str, space: str, description: str
) -> chromadb.Collection:
    """Return a collection for explicit configuration values, creating it if it is absent."""
    metadata = {"description": description}
    configuration = collection_configuration(space)
    if configuration is None:
        return client.get_or_create_collection(
            name=name, metadata=metadata | {"hnsw:space": space}
        )
    try:
        return client.get_or_create_collection(
            name=name, configuration=configuration, metadata=metadata
        )
    except TypeError as error:
        raise PipelineError(
            f"chromadb {chromadb.__version__} rejected the collection configuration "
            f"({error}); expected CollectionConfiguration or a plain hnsw mapping"
        ) from error


def get_collection(settings: Settings | None = None) -> chromadb.Collection:
    """Return the configured collection, creating it with a cosine space if it is absent."""
    resolved = settings or load_settings()
    return _collection_for(
        _client_for(str(resolved.paths.resolve("chroma_dir"))),
        resolved.chroma.collection_name,
        resolved.chroma.space,
        resolved.chroma.description,
    )


def open_collection(settings: Settings | None = None) -> chromadb.Collection:
    """Return the collection for the query path, creating a fresh handle each call.

    The collection handle can go stale if the collection is deleted and recreated (e.g. during
    re-ingestion). Creating a fresh handle per request is cheap (~1-2ms) and guarantees we never
    hold a dead UUID.
    """
    return get_collection(settings)


def metadata_for(chunk: ChunkRecord) -> dict[str, str | int]:
    """Return the Chroma metadata for one chunk, with no value that Chroma would reject.

    `section_type` is stored as its string value rather than the enum, because the enum's repr
    would have to be parsed back and a rename would then silently change what is stored.
    """
    return {
        "chunk_id": chunk.chunk_id,
        "source_id": chunk.source_id,
        "scheme_id": chunk.scheme_id,
        "scheme_name": chunk.scheme_name,
        "section": chunk.section,
        "section_type": chunk.section_type.value,
        "url": chunk.url,
        "title": chunk.title,
        "fetched_at": chunk.fetched_at,
        "ordinal": int(chunk.ordinal),
        "token_count": int(chunk.token_count),
    }


def embed_text_for(metadata: dict[str, Any], document: str) -> str:
    """Rebuild `ChunkRecord.embed_text` from a stored document and its metadata.

    The header is derived rather than stored, because it is exactly `f"[{scheme}] {section}"`
    (architecture.md §8.3) and storing it would duplicate every chunk's first line.
    """
    return f"[{metadata['scheme_name']}] {metadata['section']}\n{document}"


def record_from(metadata: dict[str, Any], document: str) -> ChunkRecord:
    """Reconstruct a full `ChunkRecord` from one stored document and its metadata."""
    return ChunkRecord(
        chunk_id=str(metadata["chunk_id"]),
        source_id=str(metadata["source_id"]),
        scheme_id=str(metadata["scheme_id"]),
        scheme_name=str(metadata["scheme_name"]),
        section=str(metadata["section"]),
        section_type=SectionType(str(metadata["section_type"])),
        text=document,
        embed_text=embed_text_for(metadata, document),
        url=str(metadata["url"]),
        title=str(metadata["title"]),
        fetched_at=str(metadata["fetched_at"]),
        ordinal=int(metadata["ordinal"]),
        token_count=int(metadata["token_count"]),
    )


def upsert_chunks(
    chunks: list[ChunkRecord], vectors: np.ndarray, settings: Settings | None = None
) -> int:
    """Write chunks and their vectors into the collection, returning how many were written.

    Upsert rather than add, keyed on the deterministic `chunk_id`, so a rebuild over the same
    corpus is idempotent and a partially built index can be topped up.
    """
    if not chunks:
        return 0
    if len(chunks) != len(vectors):
        raise PipelineError(
            f"upsert_chunks got {len(chunks)} chunks but {len(vectors)} vectors; "
            "the embedding and the chunk set are out of step"
        )
    missing = set(METADATA_KEYS) - set(metadata_for(chunks[0]))
    if missing:
        raise PipelineError(f"chunk metadata is missing {sorted(missing)}")
    collection = get_collection(settings)
    collection.upsert(
        ids=[chunk.chunk_id for chunk in chunks],
        embeddings=np.asarray(vectors, dtype="float32").tolist(),
        documents=[chunk.text for chunk in chunks],
        metadatas=[metadata_for(chunk) for chunk in chunks],
    )
    return len(chunks)


def delete_missing(keep_ids: list[str], settings: Settings | None = None) -> int:
    """Delete stored ids that are not in `keep_ids`, returning how many were removed.

    Upsert alone is not a build. If the corpus changes — a source is dropped, a section splits
    differently — the chunks that no longer exist stay in the collection, `count()` exceeds the
    real chunk count, and retrieval keeps serving text that is not in `data/chunks.jsonl`.
    """
    collection = get_collection(settings)
    existing = list((collection.get(include=[]) or {}).get("ids", []) or [])
    keep = set(keep_ids)
    stale = [stored for stored in existing if stored not in keep]
    if stale:
        collection.delete(ids=stale)
    return len(stale)


def query(
    vector: np.ndarray,
    n_results: int,
    scheme_id: str | None = None,
    settings: Settings | None = None,
    doc_type_filter: str | None = None,
) -> list[ScoredChunk]:
    """Return up to `n_results` chunks ranked by cosine similarity, with the scheme filter applied.

    The similarity is recomputed as the dot product of the query with each returned chunk's stored
    vector, not as `1 - distance` from Chroma. chromadb 0.5.23's cosine space returns
    `2 - 2·cos` — a self-match is 0.0 and an orthogonal pair is 2.0 — so `1 - distance` would
    report every unrelated chunk as a similarity of 0.0. Both vectors are L2-normalised unit
    vectors, so the dot product is the cosine similarity exactly, whatever the installed chromadb
    calls its distance (architecture.md §9). HNSW still chooses the candidate set; only the score
    of a candidate is recomputed, which is what makes the printed percentage trustworthy.
    """
    resolved = settings or load_settings()
    collection = open_collection(resolved)
    total = collection.count()
    if not total:
        raise IndexNotBuiltError(
            "the vector store is empty; run `python -m src.pipeline build` first"
        )
    query_vector = np.asarray(vector, dtype="float32")

    # Build where clause - ChromaDB supports $eq, $in, $nin, $gt, $gte, $lt, $lte, $and, $or, $not
    where_clause = None
    if scheme_id and doc_type_filter:
        # For education content, filter by known EDU source IDs
        edu_ids = [f"EDU{i:02d}" for i in range(1, 21)]
        where_clause = {"$and": [{"scheme_id": scheme_id}, {"source_id": {"$in": edu_ids}}]}
    elif scheme_id:
        where_clause = {"scheme_id": scheme_id}
    elif doc_type_filter:
        # Use $in with known EDU source IDs for education content
        edu_ids = [f"EDU{i:02d}" for i in range(1, 21)]
        where_clause = {"source_id": {"$in": edu_ids}}

    result = collection.query(
        query_embeddings=[query_vector.tolist()],
        n_results=min(n_results, total),
        where=where_clause,
        include=["documents", "metadatas", "embeddings"],
    )
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    embeddings = (result.get("embeddings") or [[]])[0]
    scored: list[ScoredChunk] = []
    for document, metadata, stored in zip(documents, metadatas, embeddings, strict=False):
        chunk = record_from(metadata, document)
        dense = min(1.0, max(0.0, float(np.asarray(stored, dtype="float32") @ query_vector)))
        scored.append(
            ScoredChunk(
                chunk=chunk,
                dense=dense,
                keyword_boost=0.0,
                final=dense,
                mmr_selected=False,
            )
        )
    return scored


def vectors_for(chunk_ids: list[str], settings: Settings | None = None) -> dict[str, np.ndarray]:
    """Return the stored embedding for each requested chunk id, for MMR redundancy scoring.

    MMR needs the pairwise similarity *between candidates* (architecture.md §11.5), which the
    query path cannot supply: it scores each candidate against the query, not against its
    neighbours. One `get` for the handful of ids in play costs about a millisecond and keeps
    `query()`'s contract — and Phase 5's tests — untouched.
    """
    collection = get_collection(settings or load_settings())
    if not chunk_ids:
        return {}
    result = collection.get(ids=list(chunk_ids), include=["embeddings"])
    ids = result.get("ids") or []
    embeddings = result.get("embeddings")
    if embeddings is None:
        return {}
    return {
        chunk_id: np.asarray(vector, dtype="float32")
        for chunk_id, vector in zip(ids, embeddings, strict=False)
        if vector is not None
    }


def stats(settings: Settings | None = None) -> dict[str, Any]:
    """Return the store's build facts: count, collection name, model id and build timestamp."""
    resolved = settings or load_settings()
    collection = get_collection(resolved)
    report = resolved.paths.resolve("chunks_dump").parent / "build_report.json"
    built_at = ""
    if report.is_file():
        try:
            built_at = str(json.loads(report.read_text(encoding="utf-8")).get("built_at", ""))
        except (OSError, json.JSONDecodeError):
            built_at = ""
    return {
        "count": collection.count(),
        "collection": resolved.chroma.collection_name,
        "space": resolved.chroma.space,
        "model_id": model_id(),
        "built_at": built_at,
        "chroma_version": chromadb.__version__,
    }


def reset(settings: Settings | None = None) -> None:
    """Delete and recreate the collection, for `build --rebuild`."""
    resolved = settings or load_settings()
    client = connect(resolved)
    name = resolved.chroma.collection_name
    existing = [found.name for found in client.list_collections()]
    if name in existing:
        client.delete_collection(name)
    get_collection(resolved)


def is_built(settings: Settings | None = None) -> bool:
    """Return True when the collection already holds at least one chunk."""
    resolved = settings if settings is not None else load_settings()
    return open_collection(resolved).count() > 0


def ensure_built(settings: Settings | None = None) -> None:
    """Raise `IndexNotBuiltError` unless the collection holds at least one chunk."""
    resolved = settings if settings is not None else load_settings()
    if not is_built(resolved):
        raise IndexNotBuiltError(
            "no chunks are indexed; run `python -m src.pipeline build` before asking a question"
        )


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string, used for the build report stamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def chroma_version_string() -> str:
    """Return the installed chromadb version, recorded in the build report."""
    return chromadb.__version__


def report_path(settings: Settings | None = None) -> Path:
    """Return the path of the persisted build report."""
    resolved = settings or load_settings()
    return resolved.paths.resolve("chunks_dump").parent / "build_report.json"
