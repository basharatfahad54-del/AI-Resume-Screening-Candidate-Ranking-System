"""Shared pytest fixtures.

The environment is configured here, at import time, because ``app.core.config``
builds and caches the settings object the first time it is imported. conftest is
loaded by pytest before any test module, which makes it the only place that is
guaranteed to run early enough to redirect the database at a throwaway file
instead of the developer's real one.

Values are assigned rather than ``setdefault``-ed on purpose: a test run must not
inherit a developer's local configuration, and none of these are secrets.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

#: Scratch directory holding the SQLite file, uploads and fixtures for one run.
WORK = Path(tempfile.mkdtemp(prefix="screening_tests_"))

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(WORK / 'test.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(WORK / "storage")
# "development" is not a shortcut here: the application only auto-creates the
# schema in that mode, because Alembic owns it everywhere else. Until the
# migration exists, any other value leaves the test database empty.
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "AdminTest123!"
os.environ["RATE_LIMIT_ENABLED"] = "true"
# High enough that the rate-limit test exercises the limiter's own threshold
# rather than tripping over the volume of ordinary requests the suite makes.
os.environ["RATE_LIMIT_PER_MINUTE"] = "500"
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["LLM_PROVIDER"] = "none"


@pytest.fixture(scope="session")
def work() -> Path:
    """Scratch directory, removed once the session finishes."""
    yield WORK
    shutil.rmtree(WORK, ignore_errors=True)


@pytest.fixture(scope="session")
def state() -> dict[str, object]:
    """Mutable bag shared by the ordered tests in ``test_api.py``.

    The API suite walks a single scenario - register, upload, rank, delete - so
    later checks need ids and tokens produced by earlier ones. Passing that
    through a session fixture keeps the tests independent of each other's local
    variables while preserving the ordering the scenario requires.
    """
    return {}


@pytest.fixture(scope="session")
def client(work: Path, state: dict[str, object]):
    """A ``TestClient`` with the application lifespan running.

    Session scoped on purpose: entering the context manager creates the schema,
    seeds the skill taxonomy and creates the bootstrap admin. Doing that per test
    would give every test a different database and make ids meaningless.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        state["client"] = test_client
        yield test_client