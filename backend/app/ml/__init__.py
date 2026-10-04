"""ML layer: embeddings, scoring, ranking and explanations.

Nothing here talks to the database. The service layer converts ORM rows into
the dataclasses defined in :mod:`app.ml.scoring`, so every function is
directly unit-testable and can be reused from the notebooks under ``ml/``.
"""

from app.ml.embeddings import (
    EmbeddingError,
    HashingEmbedder,
    active_provider,
    cosine_similarity,
    deserialize_embedding,
    embed_text,
    embed_texts,
    embedder_signature,
    get_embedder,
    matches_signature,
    serialize_embedding,
    similarity_matrix,
)
from app.ml.explanation import build_evidence_rows, build_explanation
from app.ml.ranking import SORT_FIELDS, RankedEntry, normalize_sort_field, rank_entries, score_band
from app.ml.scoring import (
    CandidateProfile,
    JobProfile,
    MatchComputation,
    SkillMatch,
    SkillSignal,
    compute_match,
)

__all__ = [
    "SORT_FIELDS",
    "CandidateProfile",
    "EmbeddingError",
    "HashingEmbedder",
    "JobProfile",
    "MatchComputation",
    "RankedEntry",
    "SkillMatch",
    "SkillSignal",
    "active_provider",
    "build_evidence_rows",
    "build_explanation",
    "compute_match",
    "cosine_similarity",
    "deserialize_embedding",
    "embed_text",
    "embed_texts",
    "embedder_signature",
    "get_embedder",
    "matches_signature",
    "normalize_sort_field",
    "rank_entries",
    "score_band",
    "serialize_embedding",
    "similarity_matrix",
]
