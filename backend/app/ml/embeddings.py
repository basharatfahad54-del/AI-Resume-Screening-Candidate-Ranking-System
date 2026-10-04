"""Text embeddings with interchangeable providers.

Providers (selected by ``EMBEDDING_PROVIDER``)
----------------------------------------------
``sentence_transformers``
    Local `all-MiniLM-L6-v2` style encoders. Best quality/cost for English
    resumes; requires the optional heavy dependency.
``openai``
    Hosted embeddings via any OpenAI-compatible endpoint.
``hashing``
    Dependency-free, fully deterministic fallback. Uses signed feature hashing
    over word and character n-grams. It is *lexical*, not semantic, but it is
    stable, offline and good enough to keep the whole pipeline runnable in CI
    and in an air-gapped evaluation.

Why the fallback matters
------------------------
Embeddings are persisted in the database. A provider switch changes the
dimension, so every stored vector becomes unusable. :func:`embedder_signature`
is stored alongside each embedding and :func:`matches_signature` refuses to
compare mismatched vectors instead of silently producing nonsense similarity.

All vectors are returned as little-endian float32, L2-normalised, so cosine
similarity reduces to a dot product.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.nlp.skill_taxonomy import normalize_text

logger = get_logger(__name__)

__all__ = [
    "EmbeddingError",
    "Embedder",
    "HashingEmbedder",
    "cosine_similarity",
    "deserialize_embedding",
    "embedder_signature",
    "embed_text",
    "embed_texts",
    "get_embedder",
    "matches_signature",
    "serialize_embedding",
]

MAX_CHARS_PER_DOC = 8000
_TOKEN = re.compile(r"[a-z0-9+#.]+")


class EmbeddingError(RuntimeError):
    """Raised when an embedding cannot be produced."""


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def serialize_embedding(vector: np.ndarray) -> bytes:
    """Pack a vector into the portable little-endian float32 blob."""
    array = np.ascontiguousarray(vector, dtype="<f4")
    return array.tobytes()


def deserialize_embedding(blob: bytes | None, dimensions: int | None = None) -> np.ndarray | None:
    """Unpack a stored blob, returning ``None`` for absent or corrupt data."""
    if not blob:
        return None
    count = len(blob) // 4
    if count == 0:
        return None
    vector = np.frombuffer(blob[: count * 4], dtype="<f4").astype(np.float64)
    if dimensions and count != dimensions:
        logger.warning(
            "Stored embedding has %s dimensions but the active model expects %s",
            count,
            dimensions,
        )
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def embedder_signature() -> str:
    """A short fingerprint of the active provider + model + dimension."""
    raw = f"{settings.embedding_provider}:{settings.embedding_model}:{settings.embedding_dimensions}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def matches_signature(blob: bytes | None, expected_dimensions: int | None = None) -> bool:
    """Whether a stored blob can be used with the active embedder.

    Only the dimensionality is checked - it is the one property that provably
    breaks a dot product, and it is cheap.
    """
    if not blob:
        return False
    if expected_dimensions is None:
        return True
    return len(blob) // 4 == expected_dimensions


def cosine_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Cosine similarity clamped to ``[-1, 1]``; 0.0 when either side is absent."""
    if a is None or b is None:
        return 0.0
    if a.shape != b.shape:
        logger.warning("Cannot compare embeddings of shape %s and %s", a.shape, b.shape)
        return 0.0
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float(np.clip(float(np.dot(a, b)) / denominator, -1.0, 1.0))


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class Embedder(ABC):
    """Common interface for every embedding backend."""

    name: str = "base"

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Length of the vectors this embedder produces."""

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Embed a batch of documents. Returns ``(n, dimensions)``."""

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class HashingEmbedder(Embedder):
    """Deterministic hashed n-gram embedder (no external dependency).

    Each token and character n-gram is hashed into a bucket with a signed
    contribution, then the vector is L2-normalised. Sub-linear term weighting
    (1 + log tf) keeps a repeated keyword from dominating the cosine score.
    """

    name = "hashing"

    def __init__(self, dimensions: int = 384, *, word_ngrams: int = 2) -> None:
        self._dimensions = max(64, dimensions)
        self._word_ngrams = word_ngrams

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @staticmethod
    def _bucket(feature: str, dimensions: int) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dimensions
        # Signed hashing keeps unrelated features from being systematically
        # correlated through the non-negativity of the counts.
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return index, sign

    def _features(self, text: str) -> dict[str, int]:
        normalized = normalize_text(text)[:MAX_CHARS_PER_DOC]
        tokens = _TOKEN.findall(normalized)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[f"w:{token}"] = counts.get(f"w:{token}", 0) + 1
        for size in range(2, self._word_ngrams + 1):
            for index in range(len(tokens) - size + 1):
                gram = "_".join(tokens[index : index + size])
                counts[f"g:{gram}"] = counts.get(f"g:{gram}", 0) + 1
        # Character trigrams give partial credit for morphological variants
        # ("postgresql" vs "postgres") and typos in short skill names.
        padded = f" {normalized} "
        for index in range(len(padded) - 2):
            gram = padded[index : index + 3]
            if gram.strip():
                counts[f"c:{gram}"] = counts.get(f"c:{gram}", 0) + 1
        return counts

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        matrix = np.zeros((len(texts), self._dimensions), dtype=np.float64)
        for row, text in enumerate(texts):
            for feature, count in self._features(text or "").items():
                index, sign = self._bucket(feature, self._dimensions)
                matrix[row, index] += sign * (1.0 + math.log(count))
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return (matrix / norms).astype(np.float32)


class SentenceTransformerEmbedder(Embedder):
    """Local sentence-transformers encoder (lazy import, loaded once)."""

    name = "sentence_transformers"

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional heavy dep
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=sentence_transformers requires "
                "'pip install sentence-transformers', or use EMBEDDING_PROVIDER=hashing"
            ) from exc
        logger.info("Loading embedding model %s", model_name)
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:  # noqa: BLE001 - includes model download failure
            raise EmbeddingError(f"Could not load embedding model '{model_name}': {exc}") from exc
        self._dimensions = int(self._model.get_sentence_embedding_dimension())

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        truncated = [(text or "")[:MAX_CHARS_PER_DOC] for text in texts]
        vectors = self._model.encode(
            truncated,
            batch_size=16,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


class OpenAIEmbedder(Embedder):
    """Hosted embeddings through any OpenAI-compatible ``/embeddings`` endpoint."""

    name = "openai"

    def __init__(self, model: str, dimensions: int) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - optional dep
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=openai requires 'pip install openai'"
            ) from exc
        if not settings.openai_api_key:
            raise EmbeddingError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=30.0, max_retries=2
        )
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        # This backend is synchronous by design: the async client is only used
        # through ``run_sync`` so the embedder interface stays uniform.
        return run_sync(self._encode_async([(text or "")[:MAX_CHARS_PER_DOC] for text in texts]))

    async def _encode_async(self, texts: list[str]) -> np.ndarray:
        response = await self._client.embeddings.create(model=self._model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        matrix = np.asarray([item.embedding for item in ordered], dtype=np.float32)
        if matrix.shape[1] != self._dimensions:
            logger.warning(
                "Model %s returned %s dimensions, configured %s",
                self._model,
                matrix.shape[1],
                self._dimensions,
            )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return matrix / norms


def run_sync(coro):
    """Run a coroutine from synchronous code, even inside a running loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop (e.g. a worker thread): use a private loop.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _Fallback:
    primary_error: str
    embedder: Embedder


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Return the configured embedder, degrading gracefully.

    A missing optional dependency or an unreachable model must not make the
    product unusable, so any failure downgrades to the hashing embedder and is
    logged loudly. Callers can inspect :func:`active_provider` to surface the
    degradation in the health endpoint.
    """
    provider = settings.embedding_provider
    try:
        if provider == "sentence_transformers":
            return SentenceTransformerEmbedder(settings.embedding_model)
        if provider == "openai":
            return OpenAIEmbedder(settings.embedding_model, settings.embedding_dimensions)
    except EmbeddingError as exc:
        logger.warning(
            "Embedding provider '%s' unavailable (%s). Falling back to the deterministic "
            "hashing embedder - semantic scores will be lexical rather than neural.",
            provider,
            exc,
        )
        return HashingEmbedder(settings.embedding_dimensions)
    return HashingEmbedder(settings.embedding_dimensions)


def active_provider() -> dict[str, object]:
    """Diagnostics for ``/api/health``."""
    embedder = get_embedder()
    degraded = embedder.name != settings.embedding_provider
    return {
        "configured": settings.embedding_provider,
        "active": embedder.name,
        "model": settings.embedding_model if not degraded else "hashed-ngram",
        "dimensions": embedder.dimensions,
        "signature": embedder_signature(),
        "degraded": degraded,
    }


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a batch. Raises :class:`EmbeddingError` on provider failure."""
    try:
        vectors = get_embedder().encode([text or "" for text in texts])
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding failed")
        raise EmbeddingError(f"Could not embed text: {exc}") from exc
    if vectors.ndim != 2 or vectors.shape[0] != len(texts):
        raise EmbeddingError("Embedding backend returned an unexpected shape")
    return vectors


def embed_text(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarity between two batches of L2-normalised vectors."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float32)
    denominator = np.outer(
        np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    )
    denominator[denominator == 0.0] = 1.0
    return np.clip((a @ b.T) / denominator, -1.0, 1.0).astype(np.float32)


def struct_size(dimensions: int) -> int:
    """Bytes occupied by a stored embedding blob."""
    return dimensions * struct.calcsize("f")
