"""Job schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import StrList


class JobRequirementsBase(BaseModel):
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    experience_required_years: float = Field(default=0.0, ge=0, le=60)
    education_required: str | None = None

    @field_validator(
        "required_skills",
        "preferred_skills",
        "certifications",
        "responsibilities",
        "soft_skills",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _dedupe(self) -> "JobRequirementsBase":
        self.required_skills = list(dict.fromkeys(self.required_skills))
        self.preferred_skills = list(dict.fromkeys(self.preferred_skills))
        return self


class JobCreate(JobRequirementsBase):
    title: str = Field(min_length=2, max_length=255)
    description: str = ""
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    weight_profile: str | None = None


class JobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    certifications: list[str] | None = None
    responsibilities: list[str] | None = None
    soft_skills: list[str] | None = None
    experience_required_years: float | None = Field(default=None, ge=0, le=60)
    education_required: str | None = None
    is_active: bool | None = None
    weight_profile: str | None = None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    department: str | None
    location: str | None
    employment_type: str | None
    description: str
    experience_required_years: float
    education_required: str | None
    # ``StrList`` so this model can be validated straight from an ORM row,
    # where these columns hold JSON text rather than a Python list.
    required_skills: StrList
    preferred_skills: StrList
    certifications: StrList
    responsibilities: StrList
    soft_skills: StrList
    source_file: str | None
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    department: str | None
    location: str | None
    is_active: bool
    candidate_count: int = 0
    created_at: datetime


class JobParseRequest(BaseModel):
    """Parse a pasted job description.

    ``persist`` defaults to False so the common case is a side-effect-free
    preview: the recruiter sees what was extracted and corrects it before it
    becomes a scored requirement.
    """

    description: str = Field(min_length=20, max_length=200_000)
    title: str | None = Field(default=None, max_length=255)
    persist: bool = False


class JobExtractionResult(BaseModel):
    """Result of LLM/heuristic parsing of an uploaded job description."""

    job_id: int | None = None
    title: str | None = None
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_required_years: float = 0.0
    education_required: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    raw_text: str = ""
