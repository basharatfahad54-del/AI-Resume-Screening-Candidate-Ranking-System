"""Candidate and resume schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.models import ProcessingStatus, SkillCategory
from app.schemas.common import StrList, coerce_obj_list


class SkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    category: SkillCategory
    description: str | None = None


class CandidateSkillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    skill: SkillRead
    confidence: float
    years_experience: float | None
    is_certified: bool
    evidence: list[str] = Field(default_factory=list)


class EmploymentEntry(BaseModel):
    title: str | None = None
    company: str | None = None
    start: str | None = None
    end: str | None = None
    raw: str | None = None


class EducationEntry(BaseModel):
    degree: str | None = None
    field: str | None = None
    institution: str | None = None
    graduation_year: int | None = None
    raw: str | None = None


class CandidateCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_title: str | None = None
    total_experience_years: float = 0.0
    education_required: str | None = None
    skills: list[str] = Field(default_factory=list)
    raw_text: str | None = None


class CandidateUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_title: str | None = None
    total_experience_years: float | None = None
    highest_degree: str | None = None
    degree_field: str | None = None
    institution: str | None = None
    graduation_year: int | None = None
    shortlisted: bool | None = None


class CandidateSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str | None
    location: str | None
    current_title: str | None
    total_experience_years: float
    highest_degree: str | None
    shortlisted: bool
    status: ProcessingStatus
    created_at: datetime
    top_skills: list[str] = Field(default_factory=list)
    overall_score: float | None = None
    rank: int | None = None


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str | None
    phone: str | None
    location: str | None
    linkedin_url: str | None
    github_url: str | None
    portfolio_url: str | None
    current_title: str | None
    total_experience_years: float
    relevant_experience_years: float
    highest_degree: str | None
    degree_field: str | None
    institution: str | None
    graduation_year: int | None
    # JSON-text columns on the ORM model, so both shapes are accepted.
    certifications: StrList = Field(default_factory=list)
    previous_positions: StrList = Field(default_factory=list)
    employment_history: Annotated[list[EmploymentEntry], BeforeValidator(coerce_obj_list)] = Field(
        default_factory=list
    )
    status: ProcessingStatus
    processing_error: str | None
    shortlisted: bool
    original_filename: str | None
    created_at: datetime
    skills: list[CandidateSkillRead] = Field(default_factory=list)


class ResumeUploadResponse(BaseModel):
    candidate: CandidateRead
    warnings: list[str] = Field(default_factory=list)


class BulkUploadResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[ResumeUploadResponse] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    batch_id: int | None = None


class CandidateSearchFilters(BaseModel):
    search: str | None = None
    skills: list[str] = Field(default_factory=list)
    min_experience: float | None = None
    max_experience: float | None = None
    education: str | None = None
    location: str | None = None
    current_title: str | None = None
    certification: str | None = None
    shortlisted_only: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
