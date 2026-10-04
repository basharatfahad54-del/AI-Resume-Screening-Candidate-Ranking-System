"""Single import point for the backend's ML primitives.

The API already ships a deterministic hashing embedder and a transparent,
six-component scoring engine. Re-implementing them here would guarantee drift,
so this module puts ``backend/`` on ``sys.path`` and imports the originals.

That gives the offline pipeline three things it could not have on its own:

* features identical to the ones the product explains to recruiters, so a model
  gain is attributable to learning rather than to a different formula;
* lexical parity between offline evaluation and the live API;
* one place to change if the scoring engine evolves.

The backend is imported read-only. Nothing in this package writes to the
application database, and the bridge never imports ``app.main`` (which would
require a database and would start the FastAPI lifespan).
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

__all__ = [
    "BACKEND_DIR",
    "CandidateProfile",
    "HashingEmbedder",
    "JobProfile",
    "MatchComputation",
    "PROJECT_ROOT",
    "ScoringWeights",
    "compute_match",
    "degree_rank",
    "normalize_skill",
    "normalize_text",
]

#: ``<root>/ml/src/talentmatch_ml/backend_bridge.py`` -> ``<root>``.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"


@lru_cache(maxsize=1)
def _ensure_backend_importable() -> Path:
    """Put ``backend/`` at the front of ``sys.path`` exactly once."""
    if not (BACKEND_DIR / "app" / "__init__.py").exists():
        raise ImportError(
            f"Backend package not found at {BACKEND_DIR}. Expected an 'app' package next to "
            "the FastAPI application; run the pipeline from a full project checkout."
        )
    path = str(BACKEND_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return BACKEND_DIR


def load_backend() -> dict[str, object]:
    """Import and return the backend symbols the pipeline depends on."""
    _ensure_backend_importable()
    from app.ml.embeddings import HashingEmbedder, cosine_similarity  # noqa: PLC0415
    from app.ml.scoring import (  # noqa: PLC0415
        CandidateProfile,
        JobProfile,
        MatchComputation,
        SkillSignal,
        compute_match,
    )
    from app.nlp.education import degree_rank  # noqa: PLC0415
    from app.nlp.skill_taxonomy import normalize_skill, normalize_text  # noqa: PLC0415
    from app.schemas.matching import ScoringWeights  # noqa: PLC0415

    return {
        "CandidateProfile": CandidateProfile,
        "HashingEmbedder": HashingEmbedder,
        "JobProfile": JobProfile,
        "MatchComputation": MatchComputation,
        "ScoringWeights": ScoringWeights,
        "SkillSignal": SkillSignal,
        "compute_match": compute_match,
        "cosine_similarity": cosine_similarity,
        "degree_rank": degree_rank,
        "normalize_skill": normalize_skill,
        "normalize_text": normalize_text,
    }


@lru_cache(maxsize=1)
def backend() -> dict[str, object]:
    """Cached :func:`load_backend` result."""
    return load_backend()


def __getattr__(name: str) -> object:
    """Expose backend symbols lazily (``from ... import compute_match``)."""
    try:
        return backend()[name]
    except KeyError as exc:  # pragma: no cover - programming error
        raise AttributeError(name) from exc