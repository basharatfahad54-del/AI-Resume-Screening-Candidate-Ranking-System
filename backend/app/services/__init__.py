"""Service layer.

Each module owns one domain (auth, jobs, resumes, matching, ...) and is the
only place allowed to combine the NLP/ML engines with the database. Routes stay
thin: they validate input, call a service, and shape the response.
"""

from __future__ import annotations

__all__ = [
    "assistant",
    "audit",
    "auth",
    "dashboard",
    "jobs",
    "matching",
    "resumes",
    "skills",
    "weights",
]