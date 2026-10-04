"""Matching, ranking, comparison and weight schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.candidate import CandidateSummary

SortField = Literal[
    "overall",
    "required_skills",
    "experience",
    "education",
    "preferred_skills",
    "semantic",
    "certifications",
]


class ScoringWeights(BaseModel):
    """Configurable component weights. Must sum to 1.0."""

    required_skills: float = Field(default=0.40, ge=0, le=1)
    experience: float = Field(default=0.20, ge=0, le=1)
    education: float = Field(default=0.15, ge=0, le=1)
    preferred_skills: float = Field(default=0.10, ge=0, le=1)
    semantic: float = Field(default=0.10, ge=0, le=1)
    certifications: float = Field(default=0.05, ge=0, le=1)

    @model_validator(mode="after")
    def _validate_sum(self) -> "ScoringWeights":
        total = self.total
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")
        return self

    @property
    def total(self) -> float:
        return (
            self.required_skills
            + self.experience
            + self.education
            + self.preferred_skills
            + self.semantic
            + self.certifications
        )


class WeightProfileCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = None
    weights: ScoringWeights
    is_default: bool = False


class WeightProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    weights: ScoringWeights
    is_default: bool
    created_at: datetime


class MatchEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    component: str
    label: str
    detail: str
    status: str
    weight: float
    snippet: str | None = None


class MatchExplanation(BaseModel):
    """Human readable, evidence backed explanation for a single candidate."""

    summary: str
    strong_matches: list[str] = Field(default_factory=list)
    preferred_matches: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    weak_signals: list[str] = Field(default_factory=list)
    experience_note: str | None = None
    education_note: str | None = None
    certification_note: str | None = None
    semantic_note: str | None = None
    recommendation: str = Field(
        default=(
            "Screening aid only. This score is generated from resume text and job "
            "requirements and must not be treated as a hiring decision."
        )
    )


class MatchResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    candidate_id: int
    required_skills_score: float
    experience_score: float
    education_score: float
    preferred_skills_score: float
    semantic_score: float
    certifications_score: float
    overall_score: float
    weights: dict[str, float] = Field(default_factory=dict)
    explanation: MatchExplanation | None = None
    evidence: list[MatchEvidenceRead] = Field(default_factory=list)
    created_at: datetime


class RankedCandidate(BaseModel):
    rank: int
    candidate: CandidateSummary
    match: MatchResultRead


class RankingResponse(BaseModel):
    job_id: int
    job_title: str
    total_candidates: int
    sort_by: SortField
    weights: ScoringWeights
    results: list[RankedCandidate] = Field(default_factory=list)
    disclaimer: str = (
        "Scores are decision-support signals computed from resume text. "
        "They do not reflect candidate suitability as a whole."
    )


class AnalyzeRequest(BaseModel):
    job_id: int
    candidate_ids: list[int] | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)
    weights: ScoringWeights | None = None
    weight_profile: str | None = None
    force: bool = False


class AnalyzeResponse(BaseModel):
    job_id: int
    analyzed: int
    skipped: int
    failed: int
    duration_ms: int
    top_candidates: list[RankedCandidate] = Field(default_factory=list)


class CompareRequest(BaseModel):
    candidate_ids: list[int] = Field(min_length=2, max_length=10)
    job_id: int | None = None
    criteria: list[str] = Field(
        default_factory=lambda: [
            "required_skills",
            "experience",
            "education",
            "preferred_skills",
            "semantic",
        ]
    )


class CandidateComparisonRow(BaseModel):
    criterion: str
    label: str
    values: dict[str, float | None]
    best: list[str] = Field(default_factory=list)
    note: str | None = None


class CompareResponse(BaseModel):
    job_id: int | None
    criteria: list[str]
    columns: list[str]
    rows: list[CandidateComparisonRow]
    skills_matrix: dict[str, list[bool]]
    disclaimer: str = (
        "Factual comparison of extracted resume data only. "
        "No autonomous hiring recommendation is produced."
    )


class ExportRequest(BaseModel):
    job_id: int
    format: Literal["csv", "json"] = "csv"
    include_explanation: bool = True
