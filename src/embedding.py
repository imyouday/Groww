"""Stage 3: turn chunk text into 384-dimensional unit vectors.

Two decisions are fixed by the brief and are not re-litigated here: the model is
`all-MiniLM-L6-v2` and the vectors are L2-normalised, which makes cosine similarity equal to the
dot product and lets the store use one distance function for both indexing and querying.

The encoder is a process-wide singleton. Loading it costs one to three seconds, and the request
path embeds one query string at a time, so a per-call load would put a three-second stall in
front of every question (D5, NFR-2). `lru_cache` on the accessor is the whole mechanism; the
`app.py` pre-warm in architecture.md §9 is what moves the cost to start-up.

The model is loaded from `paths.model_cache_dir` and never on the request path, so a built
`data/models/` is what makes the demo runnable with no network (NFR-4).
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import Settings, load_settings
from src.models import PipelineError

EMBED_DIM = 384


@lru_cache(maxsize=1)
def get_encoder() -> SentenceTransformer:
    """Return the process-wide encoder, loading it on first use."""
    settings = load_settings()
    return SentenceTransformer(
        settings.embedding.model_id,
        cache_folder=str(settings.paths.resolve("model_cache_dir")),
        device=settings.embedding.device,
    )


@lru_cache(maxsize=1)
def model_id() -> str:
    """Return the configured model id, which is recorded in the store and the build report."""
    return load_settings().embedding.model_id


def embed(texts: list[str], settings: Settings | None = None) -> np.ndarray:
    """Return an (n, 384) float32 array of L2-normalised vectors, one per input text.

    `normalize_embeddings=True` is what makes the store's cosine space equal to a dot product, so
    a similarity printed in the UI is the same number the ranking used.
    """
    resolved = settings or load_settings()
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype="float32")
    vectors = get_encoder().encode(
        texts,
        batch_size=resolved.embedding.batch_size,
        normalize_embeddings=resolved.embedding.normalize,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    array = np.asarray(vectors, dtype="float32")
    if array.shape[1] != EMBED_DIM:
        raise PipelineError(
            f"embedding dimension is {array.shape[1]}, expected {EMBED_DIM} for "
            f"{resolved.embedding.model_id}; the model and the store's collection disagree"
        )
    return array


def embed_query(text: str, settings: Settings | None = None) -> np.ndarray:
    """Return the (384,) float32 unit vector for one query string."""
    return embed([text], settings)[0]
