"""Application configuration.

All settings are environment driven. Secrets are read from environment
variables only and are never committed to the repository (see .env.example).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT.parent / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- Application ----------------
    app_name: str = "AI Resume Screening"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = True
    api_v1_prefix: str = "/api"
    log_level: str = "INFO"
    #: ``None`` means "decide from the environment": interactive docs in
    #: development and test, off in staging and production, where an exposed
    #: OpenAPI schema is a free inventory of every route and parameter. Set it
    #: explicitly to override.
    expose_api_docs: bool | None = None

    # ---------------- Database ----------------
    database_url: str = "sqlite+aiosqlite:///./storage/app.db"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---------------- Security ----------------
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    password_min_length: int = 8
    bcrypt_rounds: int = Field(
        default=12,
        ge=4,
        le=16,
        description="bcrypt cost factor. Lower it only for test runs - each login pays this cost.",
    )

    # ---------------- CORS ----------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ---------------- Uploads ----------------
    storage_dir: Path = Path("./storage")
    max_upload_size_mb: int = 10
    allowed_resume_extensions: list[str] = Field(
        default_factory=lambda: [".pdf", ".docx", ".txt"]
    )
    retention_days: int = 365

    # ---------------- Rate limiting ----------------
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60

    # ---------------- AI providers ----------------
    embedding_provider: Literal["sentence_transformers", "openai", "hashing"] = (
        "sentence_transformers"
    )
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_provider: Literal["openai", "none"] = "none"
    llm_model: str = "gpt-4o-mini"

    # ---------------- Ranking weights ----------------
    w_required_skills: float = 0.40
    w_experience: float = 0.20
    w_education: float = 0.15
    w_preferred_skills: float = 0.10
    w_semantic: float = 0.10
    w_certifications: float = 0.05

    # ---------------- Bootstrap admin ----------------
    admin_email: str = "admin@example.com"
    admin_password: str = "ChangeMe123!"

    @field_validator("cors_origins", "allowed_resume_extensions", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("storage_dir", mode="after")
    @classmethod
    def _abs_storage(cls, value: Path) -> Path:
        if not value.is_absolute():
            return (BACKEND_ROOT / value).resolve()
        return value

    @model_validator(mode="after")
    def _check_weights(self) -> "Settings":
        total = (
            self.w_required_skills
            + self.w_experience
            + self.w_education
            + self.w_preferred_skills
            + self.w_semantic
            + self.w_certifications
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Ranking weights must sum to 1.0, got {total:.4f}")
        if self.environment == "production" and self.secret_key == "change-me-in-production":
            raise ValueError("SECRET_KEY must be set to a strong value in production")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def resumes_dir(self) -> Path:
        return self.storage_dir / "resumes"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
