"""Assistant, dashboard and misc schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AssistantQuery(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    job_id: int | None = None
    conversation_id: int | None = None
    top_k: int = Field(default=10, ge=1, le=50)


class TablePayload(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    title: str | None = None


class AssistantResponse(BaseModel):
    answer: str
    conversation_id: int | None = None
    tables: list[TablePayload] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    intent: str | None = None
    mode: Literal["structured_query", "llm"] = "structured_query"
    suggestions: list[str] = Field(default_factory=list)


class DashboardStats(BaseModel):
    total_candidates: int = 0
    active_jobs: int = 0
    candidates_processed: int = 0
    average_match_score: float = 0.0
    shortlisted_candidates: int = 0
    total_skills: int = 0
    failed_processing: int = 0


class SkillCount(BaseModel):
    skill: str
    normalized_name: str
    category: str
    count: int


class ScoreBucket(BaseModel):
    bucket: str
    count: int


class JobCandidateCount(BaseModel):
    job_id: int
    job_title: str
    candidate_count: int
    average_score: float


class MissingSkillCount(BaseModel):
    job_id: int
    skill: str
    missing_count: int


class DashboardResponse(BaseModel):
    stats: DashboardStats
    candidates_per_job: list[JobCandidateCount] = Field(default_factory=list)
    score_distribution: list[ScoreBucket] = Field(default_factory=list)
    top_skills: list[SkillCount] = Field(default_factory=list)
    most_missing_skills: list[MissingSkillCount] = Field(default_factory=list)
    recent_candidates: list[dict[str, Any]] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    embedding_provider: str
    llm_provider: str


class Message(BaseModel):
    role: str
    content: str
    created_at: str | None = None
