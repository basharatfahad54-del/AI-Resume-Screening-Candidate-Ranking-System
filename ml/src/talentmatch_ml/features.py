"""Feature extraction: turns (job, candidate) pairs into a numeric matrix.

Design rule
-----------
Every feature must be something a recruiter could be told. The learned model is
a *reranker over interpretable signals*, not a black box that invents its own
representation from raw text. That is why the features come from the backend's
scoring engine, plus a handful of coverage/count statistics and one text
similarity channel that can recover expertise the candidate did not declare.

Protected attributes (``gender``, ``birth_year``) are structurally absent: the
feature builder only reads the fields listed in ``PROTECTED_CANDIDATE_FIELDS``'
complement, and :func:`feature_names` is asserted against that contract in
``ml/tests/test_fairness.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .backend_bridge import (
    CandidateProfile,
    HashingEmbedder,
    JobProfile,
    ScoringWeights,
    SkillSignal,
    compute_match,
    degree_rank,
    normalize_skill,
)

__all__ = ["FeatureBuilder", "FeatureTable", "build_feature_table"]

#: Fields on the candidate record that must never become a feature.
FORBIDDEN_FIELDS = ("gender", "birth_year")


def _skill_signals(candidate) -> tuple[SkillSignal, ...]:
    """Turn the candidate's declared skills into scored signals.

    Declared skills are treated as high confidence; a skill that also shows up
    in the free-text summary gets full confidence, otherwise a slightly lower
    but still "matched" value. The rule mirrors how the API treats parser
    output, so offline and online scoring agree.
    """
    summary = (candidate.summary or "").lower()
    signals: list[SkillSignal] = []
    for skill in candidate.declared_skills:
        normalized, _category, display = normalize_skill(skill)
        in_summary = skill.lower() in summary
        signals.append(
            SkillSignal(
                normalized_name=normalized,
                display_name=display,
                confidence=0.95 if in_summary else 0.8,
                evidence=f"Declared in the resume ({'skills list and summary' if in_summary else 'skills list'})",
            )
        )
    return tuple(signals)


def _embedding_texts(embedder: HashingEmbedder, corpus) -> dict[int, np.ndarray]:
    job_vectors = embedder.encode([job.text for job in corpus.jobs])
    candidate_vectors = embedder.encode([candidate.text for candidate in corpus.candidates])
    mapping: dict[int, np.ndarray] = {}
    for job, vector in zip(corpus.jobs, job_vectors, strict=True):
        mapping[job.job_id] = vector
    for candidate, vector in zip(corpus.candidates, candidate_vectors, strict=True):
        mapping[-candidate.candidate_id] = vector
    return mapping


@dataclass(slots=True)
class FeatureBuilder:
    """Fits text vectorisers once, then scores arbitrary pairs."""

    weights: ScoringWeights = field(default_factory=ScoringWeights)
    embedder: HashingEmbedder = field(default_factory=lambda: HashingEmbedder(384))
    _embeddings: dict[int, np.ndarray] = field(default_factory=dict, init=False)
    _vectoriser: TfidfVectorizer | None = field(default=None, init=False)
    _job_vectors: object = field(default=None, init=False)
    _candidate_vectors: object = field(default=None, init=False)
    _job_index: dict[int, int] = field(default_factory=dict, init=False)
    _candidate_index: dict[int, int] = field(default_factory=dict, init=False)

    def fit(self, corpus) -> "FeatureBuilder":
        """Pre-compute document embeddings and fit TF-IDF on the corpus text."""
        self._embeddings = _embedding_texts(self.embedder, corpus)
        self._job_index = {job.job_id: index for index, job in enumerate(corpus.jobs)}
        self._candidate_index = {
            candidate.candidate_id: index for index, candidate in enumerate(corpus.candidates)
        }
        self._vectoriser = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=2,
            sublinear_tf=True,
            norm="l2",
        )
        corpus_texts = [job.text for job in corpus.jobs] + [
            candidate.text for candidate in corpus.candidates
        ]
        matrix = self._vectoriser.fit_transform(corpus_texts)
        self._job_vectors = matrix[: len(corpus.jobs)]
        self._candidate_vectors = matrix[len(corpus.jobs) :]
        return self

    def _tfidf_similarity(self, job_id: int, candidate_id: int) -> float:
        """Cosine similarity of TF-IDF vectors, in [0, 1]."""
        if self._vectoriser is None:
            return 0.0
        value = linear_kernel(
            self._job_vectors[self._job_index[job_id]],
            self._candidate_vectors[self._candidate_index[candidate_id]],
        )
        return float(max(0.0, min(1.0, value[0, 0])))

    def featurise(self, job, candidate) -> tuple[np.ndarray, dict[str, float]]:
        """Feature vector plus the intermediate components (kept for the report)."""
        job_embedding = self._embeddings.get(job.job_id)
        candidate_embedding = self._embeddings.get(-candidate.candidate_id)
        job_profile = JobProfile(
            job_id=job.job_id,
            title=job.title,
            required_skills=tuple(job.required_skills),
            preferred_skills=tuple(job.preferred_skills),
            certifications=tuple(job.certifications),
            experience_required_years=job.experience_required_years,
            education_required=job.education_required,
            text=job.text,
            embedding=job_embedding,
        )
        candidate_profile = CandidateProfile(
            candidate_id=candidate.candidate_id,
            full_name=candidate.full_name,
            skills=_skill_signals(candidate),
            total_experience_years=candidate.years_experience,
            relevant_experience_years=candidate.years_experience,
            highest_degree=candidate.highest_degree,
            certifications=tuple(candidate.certifications),
            text=candidate.text,
            embedding=candidate_embedding,
        )
        computation = compute_match(job_profile, candidate_profile, self.weights)

        matched = len(computation.matched_required)
        weak = len(computation.weak_required)
        missing = len(computation.missing_required)
        required_total = max(1, len(job.required_skills))
        preferred_total = max(1, len(job.preferred_skills))
        applicable = computation.applicable
        active_weight = sum(
            self.weights.model_dump()[key] for key, is_on in applicable.items() if is_on
        )
        experience_gap = max(0.0, job.experience_required_years - candidate.years_experience)
        degree_gap = 0
        if job.education_required:
            degree_gap = max(
                0, degree_rank(job.education_required) - degree_rank(candidate.highest_degree)
            )
        tfidf = self._tfidf_similarity(job.job_id, candidate.candidate_id)
        raw_cosine = float(np.dot(job_embedding, candidate_embedding)) if (
            job_embedding is not None and candidate_embedding is not None
        ) else 0.0

        features: dict[str, float] = {
            # Components produced by the shipped scoring engine.
            "required_skills_score": computation.required_skills_score,
            "experience_score": computation.experience_score,
            "education_score": computation.education_score,
            "preferred_skills_score": computation.preferred_skills_score,
            "semantic_score": computation.semantic_score,
            "certifications_score": computation.certifications_score,
            "overall_score": computation.overall_score,
            # Coverage detail: how much of the requirement list was satisfied.
            "matched_required_ratio": matched / required_total,
            "weak_required_ratio": weak / required_total,
            "missing_required_ratio": missing / required_total,
            "matched_preferred_ratio": sum(
                1 for item in computation.preferred_matches if item.is_satisfied
            ) / preferred_total,
            "matched_certifications": float(
                sum(1 for item in computation.certification_matches if item.is_satisfied)
            ),
            # Absoluteness: a 6-of-6 match and a 2-of-2 match differ from 3-of-6.
            "matched_required_count": float(matched),
            "missing_required_count": float(missing),
            # Fit gaps, which are more informative than clipped ratios.
            "experience_gap_years": experience_gap,
            "experience_overqualified": float(
                candidate.years_experience - job.experience_required_years > 3.0
            ),
            "degree_gap_levels": float(degree_gap),
            # Text channels: lexical overlap can surface undeclared expertise.
            "tfidf_cosine": tfidf,
            "hashed_cosine": raw_cosine,
            # How much of the weighting profile actually applied to this pair.
            "active_weight": active_weight,
            "applicable_components": float(sum(1 for value in applicable.values() if value)),
            # Volume signals.
            "candidate_text_length": float(len(candidate.text.split())),
            "job_required_count": float(len(job.required_skills)),
            "same_role_family": float(candidate.role_family == job.role_family),
        }
        vector = np.asarray([features[name] for name in feature_names()], dtype=np.float64)
        return vector, features

    def fit_indices(self, corpus) -> "FeatureBuilder":
        self._register_indices(corpus)
        return self


#: Column order is the contract between training and inference; append only.
_FEATURE_ORDER = [
    "required_skills_score",
    "experience_score",
    "education_score",
    "preferred_skills_score",
    "semantic_score",
    "certifications_score",
    "overall_score",
    "matched_required_ratio",
    "weak_required_ratio",
    "missing_required_ratio",
    "matched_preferred_ratio",
    "matched_certifications",
    "matched_required_count",
    "missing_required_count",
    "experience_gap_years",
    "experience_overqualified",
    "degree_gap_levels",
    "tfidf_cosine",
    "hashed_cosine",
    "active_weight",
    "applicable_components",
    "candidate_text_length",
    "job_required_count",
    "same_role_family",
]


def feature_names() -> list[str]:
    """Ordered feature column names."""
    return list(_FEATURE_ORDER)


@dataclass(slots=True)
class FeatureTable:
    """Feature matrix plus everything needed to score it per query."""

    X: np.ndarray
    binary_labels: np.ndarray
    grades: np.ndarray
    groups: np.ndarray
    job_ids: list[int]
    candidate_ids: list[int]
    columns: dict[str, dict[str, float]]
    names: list[str] = field(default_factory=feature_names)

    def __len__(self) -> int:
        return int(self.X.shape[0])

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.X.shape[0]), int(self.X.shape[1]))

    def subset(self, mask: np.ndarray) -> "FeatureTable":
        indices = np.flatnonzero(mask)
        return FeatureTable(
            X=self.X[indices],
            binary_labels=self.binary_labels[indices],
            grades=self.grades[indices],
            groups=self.groups[indices],
            job_ids=[self.job_ids[index] for index in indices],
            candidate_ids=[self.candidate_ids[index] for index in indices],
            columns={name: dict(values) for name, values in self.columns.items()},
            names=list(self.names),
        )

    def per_query_scores(
        self, scores: Sequence[float], *, binary: bool = False
    ) -> dict[int, list[tuple[float, int]]]:
        """Group row scores by job id, pairing each score with its grade."""
        grouped: dict[int, list[tuple[float, int]]] = {}
        for job_id, score, label, binary_label in zip(
            self.groups, scores, self.grades, self.binary_labels, strict=True
        ):
            job_key = int(job_id)
            relevance = int(binary_label) if binary else int(label)
            grouped.setdefault(job_key, []).append((float(score), relevance))
        return grouped


def build_feature_table(corpus, *, relevance_threshold: int = 2) -> FeatureTable:
    """Featurise every labelled pair in ``corpus``."""
    builder = FeatureBuilder().fit(corpus)

    job_lookup = {job.job_id: job for job in corpus.jobs}
    candidate_lookup = {candidate.candidate_id: candidate for candidate in corpus.candidates}

    rows: list[np.ndarray] = []
    grades: list[int] = []
    groups: list[int] = []
    job_ids: list[int] = []
    candidate_ids: list[int] = []
    columns: dict[str, dict[str, float]] = {}

    for label in corpus.labels:
        job = job_lookup[label.job_id]
        candidate = candidate_lookup[label.candidate_id]
        vector, components = builder.featurise(job, candidate)
        rows.append(vector)
        grades.append(label.relevance)
        groups.append(label.job_id)
        job_ids.append(label.job_id)
        candidate_ids.append(label.candidate_id)
        for name, value in components.items():
            columns.setdefault(name, {})[f"{label.job_id}:{label.candidate_id}"] = value

    grade_array = np.asarray(grades, dtype=np.int32)
    return FeatureTable(
        X=np.vstack(rows) if rows else np.zeros((0, len(_FEATURE_ORDER))),
        binary_labels=(grade_array >= relevance_threshold).astype(np.int32),
        grades=grade_array,
        groups=np.asarray(groups, dtype=np.int32),
        job_ids=job_ids,
        candidate_ids=candidate_ids,
        columns=columns,
    )