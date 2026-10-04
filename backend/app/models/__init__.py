"""ORM models.

Design notes
------------
* Resume documents are stored outside the database (see ``app.utils.storage``);
  only a non-guessable relative path is persisted.
* Embeddings are persisted as little-endian float32 blobs so the exact same
  schema works on SQLite and PostgreSQL. ``pgvector`` can be enabled later
  without changing application code (see ``docs/architecture.md``).
* Sensitive attributes (gender, age, photo, ...) are deliberately **not**
  modelled. See ``docs/responsible-ai.md``.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base, TimestampMixin


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class UserRole(str, enum.Enum):
    admin = "admin"
    recruiter = "recruiter"
    hiring_manager = "hiring_manager"


class SkillCategory(str, enum.Enum):
    programming_language = "programming_language"
    framework = "framework"
    database = "database"
    cloud = "cloud"
    ai_ml = "ai_ml"
    devops = "devops"
    data_science = "data_science"
    frontend = "frontend"
    mobile = "mobile"
    testing = "testing"
    soft_skill = "soft_skill"
    certification = "certification"
    other = "other"


class MatchStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ProcessingStatus(str, enum.Enum):
    uploaded = "uploaded"
    parsing = "parsing"
    parsed = "parsed"
    failed = "failed"


# --------------------------------------------------------------------------- #
# Users / auth
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=32), default=UserRole.recruiter, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    jobs: Mapped[list["Job"]] = relationship(back_populates="creator", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User {self.id} {self.email} {self.role}>"


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship()


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    department: Mapped[str | None] = mapped_column(String(150))
    location: Mapped[str | None] = mapped_column(String(150))
    employment_type: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    experience_required_years: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    education_required: Mapped[str | None] = mapped_column(String(120))

    required_skills: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    preferred_skills: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    certifications: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    responsibilities: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    soft_skills: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    source_file: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    creator: Mapped[User | None] = relationship(back_populates="jobs")
    job_skills: Mapped[list["JobSkill"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    matches: Mapped[list["MatchResult"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)

    __table_args__ = (
        CheckConstraint(
            "experience_required_years >= 0", name="experience_required_years_non_negative"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.id} {self.title!r}>"


class JobSkill(Base):
    __tablename__ = "job_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    importance: Mapped[str] = mapped_column(String(20), default="required", nullable=False)
    minimum_years: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    job: Mapped[Job] = relationship(back_populates="job_skills")
    skill: Mapped["Skill"] = relationship(back_populates="job_skills")

    __table_args__ = (UniqueConstraint("job_id", "skill_id", name="uq_job_skills_job_id_skill_id"),)


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
class Skill(Base, TimestampMixin):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    category: Mapped[SkillCategory] = mapped_column(
        Enum(SkillCategory, native_enum=False, length=40),
        default=SkillCategory.other,
        nullable=False,
    )
    aliases: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    candidate_skills: Mapped[list["CandidateSkill"]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )
    job_skills: Mapped[list["JobSkill"]] = relationship(back_populates="skill")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Skill {self.normalized_name}>"


class CandidateSkill(Base):
    __tablename__ = "candidate_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    years_experience: Mapped[float | None] = mapped_column(Float)
    is_certified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    candidate: Mapped["Candidate"] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship(back_populates="candidate_skills")

    __table_args__ = (
        UniqueConstraint("candidate_id", "skill_id", name="uq_candidate_skills_candidate_id_skill_id"),
    )


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #
class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(60))
    location: Mapped[str | None] = mapped_column(String(150), index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(400))
    github_url: Mapped[str | None] = mapped_column(String(400))
    portfolio_url: Mapped[str | None] = mapped_column(String(400))

    current_title: Mapped[str | None] = mapped_column(String(200), index=True)
    total_experience_years: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevant_experience_years: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    highest_degree: Mapped[str | None] = mapped_column(String(120))
    degree_field: Mapped[str | None] = mapped_column(String(150))
    institution: Mapped[str | None] = mapped_column(String(200))
    graduation_year: Mapped[int | None] = mapped_column(Integer)

    certifications: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    previous_positions: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    employment_history: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sections: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    resume_path: Mapped[str | None] = mapped_column(String(500), nullable=False, default="")
    original_filename: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, native_enum=False, length=20),
        default=ProcessingStatus.uploaded,
        nullable=False,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    shortlisted: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)

    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)

    skills: Mapped[list[CandidateSkill]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )
    matches: Mapped[list["MatchResult"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_candidates_status_deleted", "status", "is_deleted"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Candidate {self.id} {self.full_name!r}>"


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
class MatchResult(Base, TimestampMixin):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True, nullable=False
    )

    required_skills_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    experience_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    education_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    preferred_skills_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    semantic_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    certifications_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0, index=True, nullable=False)

    weights: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    job: Mapped[Job] = relationship(back_populates="matches")
    candidate: Mapped[Candidate] = relationship(back_populates="matches")
    evidence: Mapped[list["MatchEvidence"]] = relationship(
        back_populates="match", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_match_results_job_id_candidate_id"),
        Index("ix_match_results_job_overall", "job_id", "overall_score"),
    )


class MatchEvidence(Base):
    """Per-signal evidence powering the explanation of a match."""

    __tablename__ = "match_evidence"

    # Integer, not BigInteger: SQLite only auto-assigns rowid for an
    # ``INTEGER PRIMARY KEY``, so a BigInteger key breaks local development with
    # "NOT NULL constraint failed: match_evidence.id".
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("match_results.id", ondelete="CASCADE"), index=True, nullable=False
    )
    component: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # matched | missing | weak
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)

    match: Mapped[MatchResult] = relationship(back_populates="evidence")


# --------------------------------------------------------------------------- #
# Configurable scoring weights
# --------------------------------------------------------------------------- #
class ScoringWeight(Base, TimestampMixin):
    """A named, recruiter-editable weighting profile.

    Weights are stored as JSON in ``payload`` so components can evolve without a
    migration, but they are validated (must sum to 1.0) on write.
    """

    __tablename__ = "scoring_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# --------------------------------------------------------------------------- #
# Batch jobs
# --------------------------------------------------------------------------- #
class BatchJob(Base, TimestampMixin):
    __tablename__ = "batch_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False, length=20),
        default=MatchStatus.pending,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# --------------------------------------------------------------------------- #
# Assistant conversations + audit
# --------------------------------------------------------------------------- #
class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(String(255))
    messages: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    # Integer for SQLite rowid autoincrement; see MatchEvidence.id.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))

    user: Mapped[User | None] = relationship(back_populates="audit_logs")

    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)


__all__ = [
    "AuditLog",
    "BatchJob",
    "Candidate",
    "CandidateSkill",
    "Conversation",
    "Job",
    "JobSkill",
    "MatchEvidence",
    "MatchResult",
    "MatchStatus",
    "ProcessingStatus",
    "RefreshToken",
    "ScoringWeight",
    "Skill",
    "SkillCategory",
    "User",
    "UserRole",
    "date",
]
