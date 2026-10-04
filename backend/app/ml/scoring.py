"""Candidate scoring: the six weighted components of the overall match score.

Design commitments
------------------
1. **No single opaque number.** Each component is computed independently and
   every matched/missed skill carries the evidence that justified it.
2. **Inapplicable components are excluded, not zeroed.** A job that lists no
   certifications must not silently cost every candidate 5%. When a component
   cannot be evaluated its weight is removed and the remaining weights are
   renormalised, so scores stay comparable within a job and the "why" is
   reported.
3. **Lexical first, semantic fallback.** A required skill is "matched" on a
   normalised-name hit. Only when that fails is embedding similarity used, and
   such a match is always reported as *weak* with its similarity value, never
   presented as a confirmed skill.
4. **Deterministic.** Same inputs give the same score. No randomness, no
   hidden state, no clock dependence beyond explicit date parsing.

The scoring engine is pure: it takes dataclasses, returns dataclasses, and
touches neither the database nor the network. The service layer maps ORM rows
onto these inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.ml.embeddings import cosine_similarity
from app.nlp.education import degree_rank
from app.nlp.skill_taxonomy import normalize_skill, normalize_text
from app.schemas.matching import ScoringWeights

__all__ = [
    "CandidateProfile",
    "JobProfile",
    "MatchComputation",
    "SkillMatch",
    "SkillSignal",
    "compute_match",
    "score_certifications",
    "score_education",
    "score_experience",
    "score_preferred_skills",
    "score_required_skills",
    "score_semantic",
]

#: Cosine similarity above which an unmatched required skill is considered
#: *partially* demonstrated by the resume as a whole. Tuned on held-out jobs by
#: the offline pipeline in ``ml/``; see ``ml/README.md`` and the report under
#: ``ml/runs/<run>/report.md`` for the measured effect on ranking quality.
SEMANTIC_SKILL_THRESHOLD = 0.45
#: Strength of a purely semantic (unconfirmed) skill match.
SEMANTIC_PARTIAL_CREDIT = 0.55
#: Confidence below which a name-confirmed skill is reported as "weak evidence".
WEAK_CONFIDENCE = 0.55

#: Cosine similarity is clamped to this range before becoming a 0-1 score.
SEMANTIC_FLOOR = 0.0
SEMANTIC_CEILING = 0.85

STATUS_MATCHED = "matched"
STATUS_WEAK = "weak"
STATUS_MISSING = "missing"


@dataclass(frozen=True, slots=True)
class SkillSignal:
    """A skill attributed to a candidate, with provenance."""

    normalized_name: str
    display_name: str
    confidence: float = 0.6
    years_experience: float | None = None
    is_certified: bool = False
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JobProfile:
    """Normalised, embedding-ready view of a job for scoring."""

    job_id: int
    title: str
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    certifications: tuple[str, ...] = ()
    experience_required_years: float = 0.0
    education_required: str | None = None
    text: str = ""
    embedding: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    """Normalised, embedding-ready view of a candidate for scoring."""

    candidate_id: int
    full_name: str
    skills: tuple[SkillSignal, ...] = ()
    total_experience_years: float = 0.0
    relevant_experience_years: float = 0.0
    highest_degree: str | None = None
    certifications: tuple[str, ...] = ()
    text: str = ""
    embedding: np.ndarray | None = None

    @property
    def experience_years(self) -> float:
        """Relevant experience when known, otherwise total tenure."""
        if self.relevant_experience_years > 0:
            return self.relevant_experience_years
        return self.total_experience_years


@dataclass(frozen=True, slots=True)
class SkillMatch:
    """Per-requirement outcome, the atom of the explanation."""

    term: str
    normalized_name: str
    importance: str
    status: str
    contribution: float
    confidence: float = 0.0
    similarity: float | None = None
    years_experience: float | None = None
    evidence: str | None = None

    @property
    def is_satisfied(self) -> bool:
        return self.status == STATUS_MATCHED


@dataclass(slots=True)
class MatchComputation:
    """Full scoring result for one candidate against one job."""

    required_skills_score: float
    experience_score: float
    education_score: float
    preferred_skills_score: float
    semantic_score: float
    certifications_score: float
    overall_score: float
    weights: dict[str, float]
    required_matches: list[SkillMatch] = field(default_factory=list)
    preferred_matches: list[SkillMatch] = field(default_factory=list)
    certification_matches: list[SkillMatch] = field(default_factory=list)
    applicable: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def missing_required(self) -> list[SkillMatch]:
        return [item for item in self.required_matches if item.status == STATUS_MISSING]

    @property
    def weak_required(self) -> list[SkillMatch]:
        return [item for item in self.required_matches if item.status == STATUS_WEAK]

    @property
    def matched_required(self) -> list[SkillMatch]:
        return [item for item in self.required_matches if item.status == STATUS_MATCHED]


# --------------------------------------------------------------------------- #
# Component scores
# --------------------------------------------------------------------------- #
def _candidate_index(skills: tuple[SkillSignal, ...]) -> dict[str, SkillSignal]:
    """Group candidate skills by normalised name, keeping the strongest."""
    index: dict[str, SkillSignal] = {}
    for signal in skills:
        current = index.get(signal.normalized_name)
        if current is None or signal.confidence > current.confidence:
            index[signal.normalized_name] = signal
    return index


def _best_evidence(signal: SkillSignal) -> str | None:
    return signal.evidence[0] if signal.evidence else None


def match_skill(
    term: str,
    index: dict[str, SkillSignal],
    candidate_embedding: np.ndarray | None,
    job_embedding: np.ndarray | None,
    *,
    importance: str,
) -> SkillMatch:
    """Resolve one requirement against a candidate's skills.

    Exact normalised-name match wins. Otherwise embedding similarity between
    the job document and the resume is used as *weak* evidence only.
    """
    normalized, _category, display = normalize_skill(term)
    signal = index.get(normalized)
    if signal is not None:
        status = STATUS_MATCHED if signal.confidence >= WEAK_CONFIDENCE else STATUS_WEAK
        return SkillMatch(
            term=term,
            normalized_name=normalized,
            importance=importance,
            status=status,
            contribution=signal.confidence,
            confidence=signal.confidence,
            years_experience=signal.years_experience,
            evidence=_best_evidence(signal),
        )

    similarity: float | None = None
    if candidate_embedding is not None and job_embedding is not None:
        similarity = cosine_similarity(job_embedding, candidate_embedding)
        if similarity >= SEMANTIC_SKILL_THRESHOLD:
            # Scale the partial credit by how strong the similarity is so a
            # borderline match never contributes as much as a confirmed skill.
            span = 1.0 - SEMANTIC_SKILL_THRESHOLD
            ratio = min(1.0, (similarity - SEMANTIC_SKILL_THRESHOLD) / span) if span > 0 else 1.0
            return SkillMatch(
                term=term,
                normalized_name=normalized,
                importance=importance,
                status=STATUS_WEAK,
                contribution=round(SEMANTIC_PARTIAL_CREDIT * ratio, 4),
                confidence=round(SEMANTIC_PARTIAL_CREDIT * ratio, 4),
                similarity=round(float(similarity), 4),
            )

    return SkillMatch(
        term=term,
        normalized_name=normalized,
        importance=importance,
        status=STATUS_MISSING,
        contribution=0.0,
        similarity=round(float(similarity), 4) if similarity is not None else None,
    )


def _coverage_score(matches: list[SkillMatch]) -> float:
    """Mean contribution across requirements.

    Deliberately *not* weighted by importance here: the importance weighting is
    already expressed by the component weights (required vs preferred).
    """
    if not matches:
        return 0.0
    return round(sum(item.contribution for item in matches) / len(matches), 4)


def score_required_skills(
    job: JobProfile, candidate: CandidateProfile
) -> tuple[float, list[SkillMatch]]:
    if not job.required_skills:
        return 0.0, []
    index = _candidate_index(candidate.skills)
    matches = [
        match_skill(term, index, candidate.embedding, job.embedding, importance="required")
        for term in job.required_skills
    ]
    return _coverage_score(matches), matches


def score_preferred_skills(
    job: JobProfile, candidate: CandidateProfile
) -> tuple[float, list[SkillMatch]]:
    if not job.preferred_skills:
        return 0.0, []
    index = _candidate_index(candidate.skills)
    matches = [
        match_skill(term, index, candidate.embedding, job.embedding, importance="preferred")
        for term in job.preferred_skills
    ]
    return _coverage_score(matches), matches


def score_certifications(
    job: JobProfile, candidate: CandidateProfile
) -> tuple[float, list[SkillMatch]]:
    if not job.certifications:
        return 0.0, []
    index = _candidate_index(candidate.skills)
    held = {normalize_text(name) for name in candidate.certifications}
    matches: list[SkillMatch] = []
    for term in job.certifications:
        normalized, _category, _display = normalize_skill(term)
        signal = index.get(normalized)
        if signal is not None:
            matches.append(
                SkillMatch(
                    term=term,
                    normalized_name=normalized,
                    importance="certification",
                    status=STATUS_MATCHED,
                    contribution=1.0,
                    confidence=signal.confidence,
                    years_experience=signal.years_experience,
                    evidence=_best_evidence(signal),
                )
            )
            continue
        # Certifications are often written loosely; also accept a textual hit.
        normalized_term = normalize_text(term)
        if normalized_term and any(normalized_term in value for value in held):
            matches.append(
                SkillMatch(
                    term=term,
                    normalized_name=normalized,
                    importance="certification",
                    status=STATUS_MATCHED,
                    contribution=1.0,
                    confidence=0.9,
                    evidence="Listed in the candidate's certifications",
                )
            )
        else:
            matches.append(
                SkillMatch(
                    term=term,
                    normalized_name=normalized,
                    importance="certification",
                    status=STATUS_MISSING,
                    contribution=0.0,
                )
            )
    return _coverage_score(matches), matches


def score_experience(job: JobProfile, candidate: CandidateProfile) -> float:
    """Linear coverage of the required years, capped at full marks.

    Over-qualification is not penalised - the job asks for a floor, not a
    ceiling. A candidate below the floor is scored proportionally, which keeps
    the explanation honest ("has 2 of 5 required years").
    """
    required = job.experience_required_years
    if required <= 0:
        return 0.0
    have = candidate.experience_years
    return round(min(1.0, max(0.0, have) / required), 4)


def score_education(job: JobProfile, candidate: CandidateProfile) -> float | None:
    """Education score, or ``None`` when it cannot be evaluated.

    Returns ``None`` when the job states no requirement *or* when the candidate's
    qualification could not be determined. In both cases the component is
    excluded from the weighted average and the reason is surfaced as a note,
    rather than defaulting to a number that would look like evidence.
    """
    if not job.education_required:
        return None
    required_rank = degree_rank(job.education_required)
    if required_rank == 0:
        return None
    candidate_rank = degree_rank(candidate.highest_degree)
    if candidate_rank == 0:
        return None
    if candidate_rank >= required_rank:
        return 1.0
    # One level short keeps meaningful partial credit (e.g. a BSc against an
    # MSc requirement is a gap, not a disqualification).
    levels_below = required_rank - candidate_rank
    return round(max(0.0, 1.0 - 0.4 * levels_below), 4)


def score_semantic(job: JobProfile, candidate: CandidateProfile) -> float:
    """Whole-document semantic similarity, rescaled to 0-1.

    Raw cosine similarity between two unrelated professional documents is
    typically near zero, and near 0.85 for strongly overlapping ones, so the
    raw value is rescaled over that range. Using the raw value directly would
    compress every candidate into a 0.3-0.5 band and destroy ranking power.
    """
    if job.embedding is None or candidate.embedding is None:
        return 0.0
    raw = cosine_similarity(job.embedding, candidate.embedding)
    span = SEMANTIC_CEILING - SEMANTIC_FLOOR
    if span <= 0:  # pragma: no cover - guarded constants
        return 0.0
    return round(max(0.0, min(1.0, (raw - SEMANTIC_FLOOR) / span)), 4)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def compute_match(
    job: JobProfile,
    candidate: CandidateProfile,
    weights: ScoringWeights | None = None,
) -> MatchComputation:
    """Score one candidate against one job and combine the components."""
    weights = weights or ScoringWeights()
    notes: list[str] = []

    required_score, required_matches = score_required_skills(job, candidate)
    preferred_score, preferred_matches = score_preferred_skills(job, candidate)
    certifications_score, certification_matches = score_certifications(job, candidate)
    experience_score = score_experience(job, candidate)
    education_score = score_education(job, candidate)
    semantic_score = score_semantic(job, candidate)

    # A component is applicable only if the job asks something AND we could
    # actually evaluate it.
    applicable = {
        "required_skills": bool(job.required_skills),
        "experience": job.experience_required_years > 0,
        "education": education_score is not None,
        "preferred_skills": bool(job.preferred_skills),
        "semantic": job.embedding is not None and candidate.embedding is not None,
        "certifications": bool(job.certifications),
    }
    if not applicable["education"] and job.education_required:
        notes.append(
            "Education could not be evaluated: the candidate's qualification was not detected."
        )
    if not applicable["semantic"]:
        notes.append("Semantic similarity was unavailable for this comparison.")
    if not applicable["required_skills"]:
        notes.append("This job lists no required skills, so required-skill scoring was skipped.")

    raw = {
        "required_skills": required_score,
        "experience": experience_score,
        "education": education_score,
        "preferred_skills": preferred_score,
        "semantic": semantic_score,
        "certifications": certifications_score,
    }
    weight_map = weights.model_dump()
    active_weight = sum(weight_map[key] for key, is_on in applicable.items() if is_on)

    if active_weight <= 0:
        # Nothing evaluable: fall back to the one signal always available.
        overall = round(max(0.0, min(1.0, semantic_score)), 4)
        notes.append("No scoring signal was applicable; the score reflects document similarity only.")
    else:
        overall = sum(
            raw[key] * weight_map[key] for key, is_on in applicable.items() if is_on
        ) / active_weight
        if active_weight < 1.0:
            skipped = ", ".join(sorted(key for key, is_on in applicable.items() if not is_on))
            notes.append(
                f"Components not applicable to this job were excluded and the remaining "
                f"weights renormalised: {skipped}."
            )

    return MatchComputation(
        required_skills_score=round(float(raw["required_skills"] or 0.0), 4),
        experience_score=round(float(raw["experience"] or 0.0), 4),
        education_score=round(float(education_score if education_score is not None else 0.0), 4),
        preferred_skills_score=round(float(raw["preferred_skills"] or 0.0), 4),
        semantic_score=round(float(raw["semantic"] or 0.0), 4),
        certifications_score=round(float(raw["certifications"] or 0.0), 4),
        overall_score=round(max(0.0, min(1.0, overall)), 4),
        weights={key: float(value) for key, value in weight_map.items()},
        required_matches=required_matches,
        preferred_matches=preferred_matches,
        certification_matches=certification_matches,
        applicable=applicable,
        notes=notes,
    )
