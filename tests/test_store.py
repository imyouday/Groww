"""Phase 5: the Chroma store, the embedding contract, and the build's reproducibility.

The vectors here are synthetic and orthogonal-by-construction rather than model output, so the
tests state facts about the *store* and not about retrieval quality. `data/chroma/` is the demo
index and a committed artefact must not depend on test order, so every test builds its own
collection under `tmp_path` (architecture.md §18.1).
"""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np
import pytest

from src import chunking, embedding, pipeline, store
from src.config import load_settings
from src.models import ChunkRecord, IndexNotBuiltError, PipelineError, SectionType

DIM = 384


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    """Return settings whose chroma_dir is a scratch directory, never the demo index."""
    base = load_settings()
    root = tmp_path_factory.mktemp("chroma")
    return replace(base, paths=replace(base.paths, chroma_dir=str(root / "store")))


def make_chunk(chunk_id: str, scheme_id: str, text: str, token_count: int) -> ChunkRecord:
    """Return a synthetic chunk with a stable id, for store round-trip tests."""
    section = "Minimum investments / Exit load"
    return ChunkRecord(
        chunk_id=chunk_id,
        source_id=f"SRC-{scheme_id}",
        scheme_id=scheme_id,
        scheme_name=f"HDFC Fund {scheme_id} - Direct Growth",
        section=section,
        section_type=SectionType.FEES,
        text=text,
        embed_text=f"[HDFC Fund {scheme_id} - Direct Growth] {section}\n{text}",
        url=f"https://groww.in/{scheme_id.lower()}",
        title=f"HDFC Fund {scheme_id}",
        fetched_at="2026-02-14",
        ordinal=3,
        token_count=token_count,
    )


def axis(index: int) -> np.ndarray:
    """Return a unit vector along one axis, so dot products are exactly 0 or 1."""
    vector = np.zeros(DIM, dtype="float32")
    vector[index] = 1.0
    return vector


@pytest.fixture
def three_chunks():
    """Return three chunks on distinct axes: S1 near the origin of the query, S2 and S3 apart."""
    return [
        make_chunk("c-sip", "S1", "Minimum amount for a monthly SIP is Rs 100.", 14),
        make_chunk("c-exit", "S2", "Exit load is nil after the lock-in period.", 11),
        make_chunk("c-nav", "S3", "The fund is managed by a dedicated equity team.", 12),
    ]


@pytest.fixture
def built(settings, three_chunks):
    """Return the vectors of the three synthetic chunks, written to the scratch store."""
    vectors = np.stack([axis(0), axis(1), axis(2)])
    written = store.upsert_chunks(three_chunks, vectors, settings)
    assert written == 3
    return vectors


def test_upsert_writes_every_chunk(settings, built) -> None:
    assert store.get_collection(settings).count() == 3
    assert store.stats(settings)["count"] == 3
    assert store.stats(settings)["collection"] == settings.chroma.collection_name
    assert store.stats(settings)["space"] == "cosine"


def test_nearest_chunk_wins(settings, built, three_chunks) -> None:
    hits = store.query(axis(1), 3, settings=settings)
    assert [hit.chunk.chunk_id for hit in hits][0] == "c-exit"
    assert hits[0].dense == pytest.approx(1.0, abs=1e-5)
    assert hits[0].final == hits[0].dense
    assert hits[0].keyword_boost == 0.0
    assert all(0.0 <= hit.dense <= 1.0 for hit in hits)
    assert len(hits) == len(three_chunks)


def test_similarities_decrease_along_the_ranking(settings, built) -> None:
    hits = store.query(axis(0), 3, settings=settings)
    assert [round(hit.dense, 5) for hit in hits] == [1.0, 0.0, 0.0]


def test_a_stored_vector_queried_against_itself_scores_one(settings, built) -> None:
    """A self-match must be exactly 1.0, which only holds if the collection's space is cosine.

    Under L2 the same pair scores 0.0, and under chromadb 0.5.23's `2 - 2·cos` distance a
    `1 - distance` conversion would score it 1.0 while an orthogonal pair scored 0.0 rather than
    -1.0. Asserting the self-match alone would still pass for `1 - distance`, so the orthogonal
    pair is asserted too.
    """
    assert store.query(axis(2), 1, settings=settings)[0].dense == pytest.approx(1.0, abs=1e-5)
    assert store.query(axis(7), 1, settings=settings)[0].dense == pytest.approx(0.0, abs=1e-5)


def test_scheme_filter_excludes_other_schemes(settings, built) -> None:
    hits = store.query(axis(0), 3, scheme_id="S2", settings=settings)
    assert [hit.chunk.chunk_id for hit in hits] == ["c-exit"]


def test_query_never_exceeds_the_collection_size(settings, built) -> None:
    assert len(store.query(axis(0), 50, settings=settings)) == 3


def test_every_field_round_trips_through_the_store(settings, built) -> None:
    """`fetched_at`, `ordinal` and `token_count` must survive, or C6 and budgeting break."""
    stored = store.query(axis(0), 1, settings=settings)[0].chunk
    assert stored == make_chunk("c-sip", "S1", "Minimum amount for a monthly SIP is Rs 100.", 14)
    assert stored.fetched_at == "2026-02-14"
    assert stored.ordinal == 3
    assert stored.token_count == 14
    assert stored.section_type is SectionType.FEES
    assert stored.embed_text.startswith("[HDFC Fund S1 - Direct Growth] Minimum investments")


def test_upsert_is_idempotent(settings, built) -> None:
    vectors = np.stack([axis(0), axis(1), axis(2)])
    assert store.upsert_chunks(
        [
            make_chunk("c-sip", "S1", "Minimum amount for a monthly SIP is Rs 100.", 14),
            make_chunk("c-exit", "S2", "Exit load is nil after the lock-in period.", 11),
            make_chunk("c-nav", "S3", "The fund is managed by a dedicated equity team.", 12),
        ],
        vectors,
        settings,
    ) == 3
    assert store.get_collection(settings).count() == 3


def test_upsert_rejects_a_mismatched_vector_count(settings, three_chunks) -> None:
    with pytest.raises(PipelineError, match="out of step"):
        store.upsert_chunks(three_chunks, np.stack([axis(0), axis(1)]), settings)


def test_querying_an_empty_store_explains_how_to_build(settings) -> None:
    store.reset(settings)
    with pytest.raises(IndexNotBuiltError, match="src.pipeline build"):
        store.query(axis(0), 3, settings=settings)
    with pytest.raises(IndexNotBuiltError, match="src.pipeline build"):
        store.ensure_built(settings)


def test_delete_missing_drops_chunks_that_left_the_corpus(settings, built) -> None:
    """A build must not leave chunks behind, or `count()` outruns the real chunk set."""
    assert store.delete_missing(["c-sip", "c-exit", "c-nav"], settings) == 0
    assert store.delete_missing(["c-sip", "c-exit"], settings) == 1
    assert store.get_collection(settings).count() == 2
    assert [hit.chunk.chunk_id for hit in store.query(axis(0), 3, settings=settings)] == [
        "c-sip",
        "c-exit",
    ]


def test_vector_dump_is_reproducible_from_the_store(settings, three_chunks, tmp_path) -> None:
    """`dump` must read the vectors back out of Chroma and agree with the chunk dump."""
    vectors = np.stack([axis(0), axis(1), axis(2)])
    store.upsert_chunks(three_chunks, vectors, settings)
    scratch = tmp_path / "artifacts"
    scratch.mkdir()
    chunk_path = chunking.write_chunks_jsonl(three_chunks, scratch / "chunks.jsonl")
    configured = replace(
        settings,
        paths=replace(
            settings.paths,
            chunks_dump=str(chunk_path),
            vectors_dump=str(scratch / "chunks_and_vectors.txt"),
        ),
    )
    written = pipeline.dump(configured)
    text = written.read_text(encoding="utf-8")
    assert written == scratch / "chunks_and_vectors.txt"
    assert "CHUNK 1 of 3" in text and "CHUNK 3 of 3" in text
    for chunk in three_chunks:
        assert chunk.chunk_id in text
    assert text.count("vector[384]") == 3
    assert text.count("vector norm   : 1.000000") == 3


def test_vector_dump_refuses_a_store_that_disagrees_with_the_chunk_dump(
    settings, three_chunks, tmp_path
) -> None:
    """A drifted document must fail the dump loudly, not produce a file that quietly agrees."""
    vectors = np.stack([axis(0), axis(1), axis(2)])
    store.upsert_chunks(three_chunks, vectors, settings)
    edited = [
        replace(chunk, text=f"{chunk.text} (edited after the index was built)")
        if index == 0
        else chunk
        for index, chunk in enumerate(three_chunks)
    ]
    scratch = tmp_path / "artifacts"
    scratch.mkdir()
    chunk_path = chunking.write_chunks_jsonl(edited, scratch / "chunks.jsonl")
    configured = replace(
        settings,
        paths=replace(
            settings.paths,
            chunks_dump=str(chunk_path),
            vectors_dump=str(scratch / "chunks_and_vectors.txt"),
        ),
    )
    with pytest.raises(PipelineError, match="out of step"):
        pipeline.dump(configured)


def test_reset_empties_the_collection(settings, built) -> None:
    store.reset(settings)
    assert store.get_collection(settings).count() == 0
    with pytest.raises(IndexNotBuiltError):
        store.ensure_built(settings)


def test_ensure_built_passes_once_there_are_chunks(settings, built) -> None:
    store.ensure_built(settings)


def test_telemetry_is_replaced_rather_than_merely_flagged_off() -> None:
    """`anonymized_telemetry=False` still constructs the Posthog client on 0.5.23 and logs an error."""
    assert issubclass(store.NoTelemetry, store.Component)
    assert store.NoTelemetry(object()).capture(object()) is None


def test_chroma_version_is_parsed_for_the_configuration_branch() -> None:
    assert store.chroma_version() >= store.MINIMUM_CHROMA_VERSION
    assert re.fullmatch(r"\d+(\.\d+)*", store.chroma_version_string())


def test_collection_space_is_cosine_not_the_l2_default() -> None:
    configuration = store.collection_configuration("cosine")
    assert configuration is not None
    assert configuration.to_json()["hnsw_configuration"]["space"] == "cosine"


def test_metadata_rejects_nothing_and_carries_every_documented_key() -> None:
    metadata = store.metadata_for(make_chunk("c", "S1", "text", 5))
    assert set(metadata) == set(store.METADATA_KEYS)
    assert metadata["section_type"] == "fees"
    assert metadata["ordinal"] == 3
    assert metadata["token_count"] == 5
    assert all(isinstance(value, (str, int)) for value in metadata.values())


def test_empty_upsert_is_a_no_op(settings) -> None:
    assert store.upsert_chunks([], np.zeros((0, DIM), dtype="float32"), settings) == 0


def test_embed_returns_normalised_float32_vectors() -> None:
    """Uses the real encoder: normalisation is the store's cosine contract (architecture.md §9)."""
    vectors = embedding.embed(["Minimum SIP amount", "Exit load is nil"])
    assert vectors.shape == (2, DIM)
    assert vectors.dtype == np.float32
    assert [float(np.linalg.norm(row)) for row in vectors] == pytest.approx([1.0, 1.0], abs=1e-5)
    assert -1.0 <= float(vectors[0] @ vectors[1]) <= 1.0


def test_embed_of_nothing_is_an_empty_matrix() -> None:
    assert embedding.embed([]).shape == (0, DIM)
    assert embedding.embed([]).dtype == np.float32


def test_embed_query_returns_one_vector() -> None:
    vector = embedding.embed_query("What is the exit load?")
    assert vector.shape == (DIM,)
    assert vector.dtype == np.float32
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)


def test_encoder_is_loaded_once() -> None:
    assert embedding.get_encoder() is embedding.get_encoder()
