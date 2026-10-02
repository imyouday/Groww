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
from src.models import ModelNotCachedError, PipelineError

EMBED_DIM = 384

# Windows ERROR_COMMITMENT_LIMIT, ERROR_SYSTEM_PAGEFILE_QUOTA/INSUFFICIENT_MEMORY, ERROR_NOT_ENOUGH_MEMORY,
# and the POSIX ENOMEM/EAGAIN family. Mapping these to "model not cached" is the misdiagnosis this
# table exists to prevent.
_RESOURCE_ERROR_CODES = frozenset({8, 1454, 1455, 39})


def _is_resource_exhaustion(error: OSError) -> bool:
    """Report whether an OSError means the machine ran out of a resource, not a missing file."""
    if isinstance(error, MemoryError):
        return True
    codes = {getattr(error, "errno", None), getattr(error, "winerror", None)}
    return bool(codes & _RESOURCE_ERROR_CODES)



@lru_cache(maxsize=1)
def get_encoder() -> SentenceTransformer:
    """Return the process-wide encoder, loading it on first use.

    The encoder is a singleton, so its configuration is read from the global settings exactly once
    and any `settings` passed to `embed()` cannot change the model. `embed()` therefore checks that
    the two agree instead of silently encoding with one model and reporting another's id.

    A machine that has never run this phase has no weights in `data/models/`, and loading then
    fails with a bare `OSError` from deep inside huggingface_hub. That is turned into
    `ModelNotCachedError` naming the cache directory and both remedies, because "it should not
    crash" is a requirement and a stack trace from a transitive dependency is not an answer.

    Only a *missing* cache, or a hub that cannot be reached, is reported as a missing cache. The
    resource-exhaustion OSErrors are re-raised instead, because the real one on this codebase was
    `OSError 1455` (Windows ERROR_COMMITMENT_LIMIT, "paging file too small") while mmap-ing the
    safetensors weights, and reporting that as "the model is not cached" sends an operator to
    re-download 87 MB forever.
    """
    settings = load_settings()
    cache_dir = settings.paths.resolve("model_cache_dir")
    if not cache_dir.exists():
        raise ModelNotCachedError(
            f"no model cache at {cache_dir}; run `python -m src.pipeline build` once while online, "
            f"or copy the data/models/ directory from a machine that already has it"
        )
    try:
        return SentenceTransformer(
            settings.embedding.model_id,
            cache_folder=str(cache_dir),
            device=settings.embedding.device,
        )
    except OSError as error:
        if _is_resource_exhaustion(error):
            raise
        raise ModelNotCachedError(
            f"could not load {settings.embedding.model_id} from {cache_dir} and could not reach "
            "the model hub; run `python -m src.pipeline build` once while online, or copy the "
            f"data/models/ directory from a machine that already has it ({cache_dir})"
        ) from error




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
    if resolved.embedding.model_id != model_id():
        raise PipelineError(
            f"the loaded encoder is {model_id()!r} but these settings ask for "
            f"{resolved.embedding.model_id!r}; the encoder is a process-wide singleton, so it "
            "cannot be reconfigured per call. Clear src.embedding.get_encoder.cache_clear() first."
        )
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
