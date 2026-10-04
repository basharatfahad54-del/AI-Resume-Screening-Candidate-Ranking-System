"""End-to-end API integration tests.

These drive the real ASGI application against a throwaway SQLite database and
storage directory, covering every router plus the authorization matrix.

The tests are **order dependent by design**: they walk one recruiter workflow
(register -> upload resumes -> rank -> administer -> delete) so that assertions
can be made against ids and tokens produced earlier in the run. pytest executes
tests in file order, which is what makes the scenario reproducible. Do not
reorder or run a single test in isolation without adding the state it needs.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

RESUME_TEXT = """
Jordan Alvarez
Senior Backend Engineer
jordan.alvarez@example.com | +1 415 555 0142 | San Francisco, CA
https://github.com/jalvarez | linkedin.com/in/jalvarez

SUMMARY
Backend engineer with eight years building distributed services in Python and Go.

EXPERIENCE
Staff Software Engineer, Northwind Logistics, 2021 - Present
- Rebuilt the order pipeline on FastAPI and PostgreSQL, cutting p99 latency 40%.
- Introduced Kubernetes and Terraform for multi-region deployment.
- Mentored four engineers; ran the hiring loop for the platform team.

Software Engineer, Cobalt Systems, 2018 - 2021
- Built ETL jobs in Python and Apache Spark processing 4TB/day.
- Introduced Docker and CI/CD with GitHub Actions.

EDUCATION
B.S. Computer Science, University of California Davis, 2014

SKILLS
Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, Terraform, GitHub Actions,
Apache Spark, SQL, Redis, Kafka

CERTIFICATIONS
AWS Certified Solutions Architect - Associate
Certified Kubernetes Application Developer (CKAD)
"""

SECOND_RESUME = """
Priya Raman
Data Analyst
priya.raman@example.com | Austin, TX

EXPERIENCE
Data Analyst, Lakeside Retail, 2020 - Present
- SQL and Python reporting for 200 stores; Tableau dashboards.
- Built ETL pipelines with Airflow.

EDUCATION
B.S. Statistics, University of Texas at Austin, 2016

SKILLS
SQL, Python, Tableau, Airflow, Excel
"""

JD_TEXT = """
We are hiring a Senior Backend Engineer.

RESPONSIBILITIES
- Design and operate distributed backend services in Python.
- Own services end to end, including on-call.

REQUIRED SKILLS
Python, FastAPI, PostgreSQL, Docker, Kubernetes

PREFERRED SKILLS
AWS, Terraform, Kafka, Redis

REQUIREMENTS
- 5+ years of backend engineering experience.
- Bachelor's degree in Computer Science or equivalent.
- Experience with CI/CD and infrastructure as code.

CERTIFICATIONS
AWS Certified Solutions Architect is a plus.
"""


def expect(condition: bool, label: str, detail: str = "") -> None:
    """Assert with the endpoint response attached.

    Plain ``assert x == y, r.text`` loses the label, and these tests cover long
    sequences where knowing *which* expectation broke is most of the value.
    """
    if not condition:
        raise AssertionError(f"{label} -> {detail}")


def bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --------------------------------------------------------------------------- #
# Health and discovery
# --------------------------------------------------------------------------- #
def test_health_and_discovery(client: TestClient) -> None:
    response = client.get("/api/health")
    expect(response.status_code == 200, "health 200", response.text)
    expect(response.json().get("database") == "ok", "health db ok", response.text)
    expect(client.get("/").status_code == 200, "root 200")
    expect(client.get("/openapi.json").status_code == 200, "openapi builds")

    # The API must not rely on a reverse proxy for these.
    expect(
        response.headers.get("x-content-type-options") == "nosniff",
        "nosniff is set by the app",
        str(dict(response.headers)),
    )
    expect(
        response.headers.get("x-frame-options") == "DENY",
        "framing is denied by the app",
        str(dict(response.headers)),
    )
    expect(
        response.headers.get("x-request-id"),
        "every response carries a request id",
        str(dict(response.headers)),
    )


def test_docs_are_off_outside_development() -> None:
    """An exposed OpenAPI schema is free reconnaissance.

    Interactive docs stay on in development and test (the CI job imports the app
    and reads the schema) and are off in staging and production unless someone
    opts in deliberately.
    """
    from app.main import docs_enabled

    expect(docs_enabled("development") is True, "docs on in development")
    expect(docs_enabled("test") is True, "docs on in test")
    expect(docs_enabled("staging") is False, "docs off in staging")
    expect(docs_enabled("production") is False, "docs off in production")
    expect(docs_enabled("production", True) is True, "explicit opt-in wins")
    expect(docs_enabled("development", False) is False, "explicit opt-out wins")


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def test_registration_rules(client: TestClient, state: dict[str, object]) -> None:
    response = client.post(
        "/api/auth/register",
        json={"name": "Rhea Kapoor", "email": "rhea@example.com", "password": "Recruiter1"},
    )
    expect(response.status_code == 201, "register 201", response.text)
    expect(
        response.json()["user"]["role"] == "recruiter",
        "register forces recruiter role",
        response.text,
    )

    response = client.post(
        "/api/auth/register",
        json={
            "name": "Escalation Attempt",
            "email": "sneaky@example.com",
            "password": "Sneaky123",
            "role": "admin",
        },
    )
    expect(
        response.status_code == 201 and response.json()["user"]["role"] == "recruiter",
        "register cannot self-grant admin",
        response.text,
    )

    response = client.post(
        "/api/auth/register",
        json={"name": "Weak", "email": "weak@example.com", "password": "weak"},
    )
    expect(response.status_code == 422, "weak password rejected", response.text)

    response = client.post(
        "/api/auth/register",
        json={"name": "Dup", "email": "rhea@example.com", "password": "Recruiter1"},
    )
    expect(response.status_code == 409, "duplicate email 409", response.text)


def test_login_and_token_handling(client: TestClient, state: dict[str, object]) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "AdminTest123!"},
    )
    expect(response.status_code == 200, "admin login 200", response.text)
    expect(response.json()["user"]["role"] == "admin", "login returns admin role", response.text)
    admin_tokens = response.json()["tokens"]
    state["admin_tokens"] = admin_tokens
    state["admin_headers"] = bearer(admin_tokens)

    response = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "WrongPass1"}
    )
    expect(response.status_code == 401, "bad password 401", response.text)

    response = client.get("/api/auth/me", headers=state["admin_headers"])
    expect(
        response.status_code == 200 and response.json()["email"] == "admin@example.com",
        "me 200",
        response.text,
    )


def test_rejected_tokens_return_401(client: TestClient, state: dict[str, object]) -> None:
    expect(client.get("/api/auth/me").status_code == 401, "me without token 401")
    expect(
        client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code
        == 401,
        "me with garbage token 401 (not 500)",
    )
    tampered = state["admin_tokens"]["access_token"][:-3] + "aaa"
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    expect(response.status_code == 401, "me with tampered token 401", response.text)


def test_refresh_rotates_and_invalidates(client: TestClient, state: dict[str, object]) -> None:
    original = state["admin_tokens"]["refresh_token"]
    response = client.post("/api/auth/refresh", json={"refresh_token": original})
    expect(response.status_code == 200, "refresh 200", response.text)
    rotated = response.json()["tokens"]["refresh_token"]
    state["rotated_refresh"] = rotated
    expect(rotated != original, "refresh rotates the token", response.text)

    response = client.post("/api/auth/refresh", json={"refresh_token": original})
    expect(response.status_code == 401, "old refresh token rejected", response.text)

    response = client.post("/api/auth/refresh", json={"refresh_token": rotated})
    state["admin_headers"] = bearer(response.json()["tokens"])
    state["admin_tokens"] = response.json()["tokens"]


def test_refresh_tokens_are_not_stored_in_replayable_form(
    client: TestClient, state: dict[str, object]
) -> None:
    """The database holds a fingerprint, never a usable token.

    Reading ``refresh_tokens`` must not be enough to impersonate a session, so
    the column contains a SHA-256 digest. This test reads the table directly
    rather than trusting the docstring.
    """
    from sqlalchemy import select as sa_select

    from app.db.session import AsyncSessionLocal
    from app.models import RefreshToken

    async def _stored() -> list[str]:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(sa_select(RefreshToken.token))).scalars().all()
            return list(rows)

    import asyncio

    stored = asyncio.run(_stored())
    expect(bool(stored), "refresh tokens are persisted")

    live = state["admin_tokens"]["refresh_token"]
    expect(
        all(len(value) == 64 for value in stored),
        "stored values are sha256 digests, not JWTs",
        str(stored[:1]),
    )
    expect(
        not any(value.count(".") == 2 for value in stored),
        "no stored row looks like a JWT",
        str(stored[:1]),
    )
    expect(live not in stored, "the live token itself is never stored")

    # And the session still works, so the fingerprint lookup is not broken.
    response = client.post("/api/auth/refresh", json={"refresh_token": live})
    expect(response.status_code == 200, "fingerprint lookup still refreshes", response.text)
    state["admin_headers"] = bearer(response.json()["tokens"])
    state["admin_tokens"] = response.json()["tokens"]


def test_recruiter_session_created(client: TestClient, state: dict[str, object]) -> None:
    response = client.post(
        "/api/auth/register",
        json={"name": "Sam Recruiter", "email": "sam@example.com", "password": "Recruiter1"},
    )
    expect(response.status_code == 201, "recruiter register 201", response.text)
    state["recruiter_headers"] = bearer(response.json()["tokens"])


# --------------------------------------------------------------------------- #
# Authorization matrix
# --------------------------------------------------------------------------- #
def test_role_enforcement(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]
    admin = state["admin_headers"]

    response = client.get("/api/admin/users", headers=recruiter)
    expect(response.status_code == 403, "recruiter blocked from admin 403", response.text)

    response = client.get("/api/admin/users", headers=admin)
    expect(response.status_code == 200, "admin lists users 200", response.text)
    expect(len(response.json()) >= 3, "user list populated", response.text)

    response = client.post("/api/skills/sync", headers=recruiter)
    expect(response.status_code == 403, "recruiter blocked from taxonomy sync 403", response.text)


def test_hiring_manager_reaches_candidate_data(client: TestClient, state: dict[str, object]) -> None:
    """A hiring manager works with candidates, but cannot change shared config.

    ``RecruiterUser`` admits ``hiring_manager``; if that role is ever dropped from
    the dependency the user is left able to sign in and see nothing at all, which
    is the failure this test exists to prevent.
    """
    admin = state["admin_headers"]

    response = client.post(
        "/api/admin/users",
        json={
            "name": "Hana Manager",
            "email": "hana@example.com",
            "password": "ManagerPass1",
            "role": "hiring_manager",
        },
        headers=admin,
    )
    expect(response.status_code == 201, "admin creates hiring manager 201", response.text)
    expect(
        response.json()["role"] == "hiring_manager",
        "role is honoured for admin-created users",
        response.text,
    )

    response = client.post(
        "/api/auth/login", json={"email": "hana@example.com", "password": "ManagerPass1"}
    )
    expect(response.status_code == 200, "hiring manager can log in", response.text)
    headers = bearer(response.json()["tokens"])

    response = client.get("/api/candidates", headers=headers)
    expect(
        response.status_code == 200,
        "hiring manager reads candidates 200 (not a 403 dead end)",
        response.text,
    )

    response = client.get("/api/jobs", headers=headers)
    expect(response.status_code == 200, "hiring manager reads jobs 200", response.text)

    response = client.get("/api/admin/users", headers=headers)
    expect(response.status_code == 403, "hiring manager blocked from admin 403", response.text)

    response = client.post(
        "/api/weights",
        json={"name": "Manager weights", "weights": {"required_skills": 1.0}},
        headers=headers,
    )
    expect(response.status_code == 403, "hiring manager cannot create weights", response.text)


# --------------------------------------------------------------------------- #
# Skills taxonomy
# --------------------------------------------------------------------------- #
def test_skill_taxonomy(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.get("/api/skills/count", headers=recruiter)
    expect(
        response.status_code == 200 and response.json()["count"] > 50,
        "skill count 200",
        response.text,
    )

    response = client.get("/api/skills", params={"search": "kubernetes"}, headers=recruiter)
    expect(response.status_code == 200 and response.json(), "skill search", response.text)
    expect(
        any(skill["category"] == "devops" for skill in response.json()),
        "skill category is canonical",
        response.text,
    )

    response = client.post("/api/skills/sync", headers=state["admin_headers"])
    expect(response.status_code == 200, "admin taxonomy sync 200", response.text)


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def test_job_crud(client: TestClient, state: dict[str, object], work: Path) -> None:
    recruiter = state["recruiter_headers"]

    response = client.post(
        "/api/jobs",
        json={
            "title": "Senior Backend Engineer",
            "description": JD_TEXT,
            "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"],
            "preferred_skills": ["AWS", "Terraform", "Kafka"],
            "certifications": ["AWS Certified Solutions Architect"],
            "responsibilities": ["Design and operate distributed backend services"],
            "experience_required_years": 5,
            "education_required": "Bachelor's degree in Computer Science",
            "department": "Engineering",
            "location": "Remote",
        },
        headers=recruiter,
    )
    expect(response.status_code == 201, "create job 201", response.text)
    job = response.json()
    state["job_id"] = job["id"]
    expect("Python" in job["required_skills"], "job has canonical skills", response.text)

    response = client.get("/api/jobs", headers=recruiter)
    expect(
        response.status_code == 200 and response.json()["total"] >= 1,
        "list jobs 200",
        response.text,
    )

    response = client.get(f"/api/jobs/{state['job_id']}", headers=recruiter)
    expect(
        response.status_code == 200 and response.json()["title"] == "Senior Backend Engineer",
        "get job 200",
        response.text,
    )

    response = client.get("/api/jobs/999999", headers=recruiter)
    expect(response.status_code == 404, "missing job 404", response.text)

    response = client.patch(
        f"/api/jobs/{state['job_id']}",
        json={"location": "Remote (US)"},
        headers=recruiter,
    )
    expect(
        response.status_code == 200 and response.json()["location"] == "Remote (US)",
        "patch job 200",
        response.text,
    )


def test_job_parse_preview_and_persist(
    client: TestClient, state: dict[str, object], work: Path
) -> None:
    recruiter = state["recruiter_headers"]

    response = client.post(
        "/api/jobs/parse", json={"description": JD_TEXT}, headers=recruiter
    )
    expect(response.status_code == 200, "parse preview 200", response.text)
    preview = response.json()
    expect(
        len(preview.get("required_skills", [])) >= 4,
        "preview found required skills",
        response.text,
    )
    expect(preview.get("job_id") is None, "preview did not persist a job", response.text)

    response = client.post(
        "/api/jobs/parse",
        json={"description": JD_TEXT, "persist": True, "title": "Parsed Backend Role"},
        headers=recruiter,
    )
    expect(response.status_code == 200, "parse persist 200", response.text)
    expect(response.json().get("job_id") is not None, "parse persist returns job_id", response.text)
    state["parsed_job_id"] = response.json()["job_id"]

    jd_file = work / "job.txt"
    jd_file.write_text(JD_TEXT, encoding="utf-8")
    with jd_file.open("rb") as handle:
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("job.txt", handle, "text/plain")},
            headers=recruiter,
        )
    expect(response.status_code == 201, "upload job file 201", response.text)


def test_job_pagination(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.get("/api/jobs?page=1&page_size=2", headers=recruiter)
    expect(len(response.json()["results"]) <= 2, "job pagination honours page_size", response.text)
    expect(response.json()["pages"] >= 2, "job pagination reports pages", response.text)
    expect(client.get("/api/jobs?page=0", headers=recruiter).status_code == 422, "page=0 422")


# --------------------------------------------------------------------------- #
# Candidates and resume upload
# --------------------------------------------------------------------------- #
def test_manual_candidate_creation(client: TestClient, state: dict[str, object]) -> None:
    response = client.post(
        "/api/candidates",
        json={
            "full_name": "Manual Entry Candidate",
            "email": "manual@example.com",
            "current_title": "Backend Engineer",
            "total_experience_years": 6,
            "location": "Berlin",
            "skills": ["Python", "PostgreSQL"],
        },
        headers=state["recruiter_headers"],
    )
    expect(response.status_code == 201, "manual candidate 201", response.text)
    state["manual_candidate_id"] = response.json()["id"]


def test_resume_upload_and_parsing(
    client: TestClient, state: dict[str, object], work: Path
) -> None:
    recruiter = state["recruiter_headers"]
    resume = work / "resume_a.txt"
    resume.write_text(RESUME_TEXT, encoding="utf-8")

    with resume.open("rb") as handle:
        response = client.post(
            "/api/candidates/upload",
            files=[("files", ("resume_a.txt", handle, "text/plain"))],
            headers=recruiter,
        )
    expect(response.status_code == 201, "upload resume 201", response.text)
    uploaded = response.json()
    expect(
        uploaded["succeeded"] == 1 and uploaded["failed"] == 0,
        "one resume succeeded",
        response.text,
    )

    profile = uploaded["results"][0]["candidate"]
    state["candidate_a"] = profile["id"]
    expect(profile["full_name"] == "Jordan Alvarez", "resume parsed a name", str(profile))
    expect(
        profile["email"] == "jordan.alvarez@example.com", "resume extracted email", str(profile)
    )
    expect(
        "Engineer" in (profile["current_title"] or ""),
        "resume extracted title",
        str(profile["current_title"]),
    )
    expect(len(profile["skills"]) >= 6, "resume extracted skills", str(profile))
    expect(
        profile["total_experience_years"] >= 7,
        "resume counts the current role in experience",
        str(profile["total_experience_years"]),
    )
    expect(
        profile["highest_degree"] == "B.S. Computer Science",
        "resume extracted a degree",
        str(profile["highest_degree"]),
    )
    expect(
        "California" in (profile["institution"] or ""),
        "resume extracted the institution",
        str(profile["institution"]),
    )
    expect(
        profile["graduation_year"] == 2014,
        "resume extracted graduation year",
        str(profile["graduation_year"]),
    )


def test_bulk_upload_and_extension_filtering(
    client: TestClient, state: dict[str, object], work: Path
) -> None:
    recruiter = state["recruiter_headers"]
    first = work / "resume_a.txt"
    second = work / "resume_b.txt"
    second.write_text(SECOND_RESUME, encoding="utf-8")
    evil = work / "evil.exe"
    evil.write_bytes(b"MZ\x90\x00")

    with second.open("rb") as handle:
        response = client.post(
            "/api/candidates/upload",
            files=[("files", ("resume_b.txt", handle, "text/plain"))],
            headers=recruiter,
        )
    expect(response.status_code == 201, "second upload 201", response.text)
    state["candidate_b"] = response.json()["results"][0]["candidate"]["id"]

    with evil.open("rb") as handle:
        response = client.post(
            "/api/candidates/upload",
            files=[("files", ("evil.exe", handle, "application/octet-stream"))],
            headers=recruiter,
        )
    expect(
        response.status_code == 201 and response.json()["failed"] == 1,
        "disallowed extension rejected without failing the batch",
        response.text,
    )

    with first.open("rb") as one, second.open("rb") as two:
        response = client.post(
            "/api/candidates/upload",
            files=[("files", ("a.txt", one, "text/plain")), ("files", ("b.txt", two, "text/plain"))],
            headers=recruiter,
        )
    expect(
        response.status_code == 201 and response.json()["succeeded"] == 2,
        "bulk upload handles several files",
        response.text,
    )
    # The duplicates are removed again so the candidate count stays predictable.
    for extra in (item["candidate"]["id"] for item in response.json()["results"]):
        client.delete(f"/api/candidates/{extra}", headers=recruiter)


# --------------------------------------------------------------------------- #
# Candidate queries
# --------------------------------------------------------------------------- #
def test_candidate_search_and_filters(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.get("/api/candidates", headers=recruiter)
    expect(response.status_code == 200, "list candidates 200", response.text)
    expect(response.json()["total"] == 3, "three candidates present", response.text)

    response = client.get("/api/candidates", params={"search": "Jordan"}, headers=recruiter)
    expect(response.json()["total"] == 1, "search by name", response.text)

    response = client.get(
        "/api/candidates", params={"min_experience": 7}, headers=recruiter
    )
    expect(response.json()["total"] == 1, "filter by min experience", response.text)

    response = client.get(
        "/api/candidates", params={"max_experience": 7}, headers=recruiter
    )
    expect(response.json()["total"] == 2, "filter by max experience", response.text)

    response = client.get(
        "/api/candidates", params={"skills": ["python", "kubernetes"]}, headers=recruiter
    )
    expect(response.json()["total"] == 1, "AND skill filter", response.text)

    response = client.get(
        "/api/candidates", params={"skills": ["tableau"]}, headers=recruiter
    )
    expect(response.json()["total"] == 1, "second candidate matches tableau", response.text)


def test_candidate_detail_documents_and_actions(
    client: TestClient, state: dict[str, object]
) -> None:
    recruiter = state["recruiter_headers"]
    candidate = state["candidate_a"]

    response = client.get(f"/api/candidates/{candidate}", headers=recruiter)
    expect(response.status_code == 200, "get candidate 200", response.text)
    body = response.json()
    expect(
        len(body["employment_history"]) >= 2, "candidate has employment history", response.text
    )
    expect(
        body["employment_history"][0]["start"] is not None,
        "candidate employment has dates",
        response.text,
    )
    expect(len(body["certifications"]) >= 1, "candidate certifications found", response.text)

    expect(
        client.get("/api/candidates/999999", headers=recruiter).status_code == 404,
        "missing candidate 404",
    )

    response = client.get(f"/api/candidates/{candidate}/sections", headers=recruiter)
    expect(response.status_code == 200 and response.json(), "candidate sections 200", response.text)

    response = client.get(f"/api/candidates/{candidate}/resume", headers=recruiter)
    expect(response.status_code == 200, "download resume 200", response.text)
    expect(
        "attachment" in response.headers.get("content-disposition", ""),
        "download sets attachment header",
        response.headers.get("content-disposition", ""),
    )
    expect(b"Jordan Alvarez" in response.content, "download streams the text")

    response = client.patch(
        f"/api/candidates/{candidate}",
        json={"current_title": "Staff Backend Engineer"},
        headers=recruiter,
    )
    expect(response.status_code == 200, "patch candidate 200", response.text)

    # Reparsing replaces the skill rows. candidate_skills is unique on
    # (candidate_id, skill_id) and most skills are unchanged, so this is the
    # regression test for delete-before-insert ordering.
    response = client.post(f"/api/candidates/{candidate}/reparse", headers=recruiter)
    expect(response.status_code == 200, "reparse 200", response.text)

    response = client.post(
        f"/api/candidates/{candidate}/shortlist",
        json={"shortlisted": True},
        headers=recruiter,
    )
    expect(
        response.status_code == 200 and response.json()["shortlisted"] is True,
        "shortlist 200",
        response.text,
    )

    response = client.get(
        "/api/candidates", params={"shortlisted_only": True}, headers=recruiter
    )
    expect(response.json()["total"] == 1, "shortlisted filter", response.text)


# --------------------------------------------------------------------------- #
# Matching, ranking and export
# --------------------------------------------------------------------------- #
def test_analyze_ranks_candidates(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]
    job_id = state["job_id"]

    response = client.post("/api/matches/analyze", json={"job_id": job_id}, headers=recruiter)
    expect(response.status_code == 200, "analyze 200", response.text)
    analyzed = response.json()
    expect(analyzed["analyzed"] == 3, "analyzed all three", response.text)
    expect(analyzed["failed"] == 0, "no failures", response.text)
    expect(analyzed["duration_ms"] >= 0, "duration reported", response.text)
    expect(len(analyzed["top_candidates"]) == 3, "top candidates returned", response.text)

    top = analyzed["top_candidates"][0]
    expect(
        top["candidate"]["full_name"] == "Jordan Alvarez",
        "strong candidate ranks first",
        str(top),
    )
    expect(
        0.0 <= top["match"]["overall_score"] <= 1.0, "score is bounded", str(top["match"])
    )
    expect(top["match"]["explanation"] is not None, "explanation present", str(top["match"]))
    expect(len(top["match"]["evidence"]) > 0, "evidence rows present", str(top["match"]))

    response = client.post(
        "/api/matches/analyze",
        json={"job_id": job_id, "candidate_ids": [state["candidate_b"]]},
        headers=recruiter,
    )
    expect(response.json()["skipped"] == 1, "analyze subset skips cached", response.text)

    response = client.post(
        "/api/matches/analyze", json={"job_id": job_id, "force": True}, headers=recruiter
    )
    expect(response.json()["analyzed"] == 3, "force re-analyzes", response.text)


def test_ranking_endpoint(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]
    job_id = state["job_id"]

    response = client.get(f"/api/jobs/{job_id}/ranking", headers=recruiter)
    expect(response.status_code == 200, "ranking 200", response.text)
    ranking = response.json()
    expect(len(ranking["results"]) == 3, "ranking returns all", response.text)
    expect(bool(ranking.get("disclaimer")), "ranking has disclaimer", response.text)

    scores = [row["match"]["overall_score"] for row in ranking["results"]]
    expect(scores == sorted(scores, reverse=True), "ranking sorted descending", str(scores))

    response = client.get(
        f"/api/jobs/{job_id}/ranking", params={"sort_by": "experience"}, headers=recruiter
    )
    expect(response.status_code == 200, "sort by experience 200", response.text)

    response = client.get(
        f"/api/jobs/{job_id}/ranking", params={"sort_by": "nonsense"}, headers=recruiter
    )
    expect(response.status_code == 422, "invalid sort field 422", response.text)

    response = client.get(
        f"/api/jobs/{job_id}/ranking",
        params={"page_size": 2, "page": 2},
        headers=recruiter,
    )
    expect(
        response.status_code == 200 and len(response.json()["results"]) == 1,
        "ranking pagination",
        response.text,
    )

    match_id = ranking["results"][0]["match"]["id"]
    response = client.get(f"/api/matches/{match_id}", headers=recruiter)
    expect(response.status_code == 200, "match detail 200", response.text)
    expect(response.json()["evidence_count"] > 0, "match detail has evidence", response.text)
    expect(bool(response.json()["disclaimer"]), "match detail has disclaimer", response.text)


def test_compare_and_export(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.post(
        "/api/matches/compare",
        json={
            "candidate_ids": [state["candidate_a"], state["candidate_b"]],
            "job_id": state["job_id"],
        },
        headers=recruiter,
    )
    expect(response.status_code == 200, "compare 200", response.text)
    comparison = response.json()
    expect(
        len(comparison["rows"]) == len(comparison["criteria"]) >= 4,
        "compare has one row per criterion",
        response.text,
    )
    expect(bool(comparison["skills_matrix"]), "compare has skills matrix", response.text)
    expect(bool(comparison.get("disclaimer")), "compare has disclaimer", response.text)

    response = client.post(
        "/api/matches/compare",
        json={"candidate_ids": [state["candidate_a"]]},
        headers=recruiter,
    )
    expect(response.status_code == 422, "compare needs two candidates", response.text)

    response = client.post(
        "/api/matches/export",
        json={"job_id": state["job_id"], "format": "csv", "include_explanation": True},
        headers=recruiter,
    )
    expect(response.status_code == 200, "export csv 200", response.text)
    expect("text/csv" in response.headers.get("content-type", ""), "csv content type")
    expect(response.text.count("\n") >= 4, "csv has rows", response.text[:200])
    expect("Jordan Alvarez" in response.text, "csv keeps the candidate name", response.text[:200])

    response = client.post(
        "/api/matches/export",
        json={"job_id": state["job_id"], "format": "json"},
        headers=recruiter,
    )
    expect(response.status_code == 200, "export json 200", response.text)
    expect("overall_score" in response.text, "json export parses", response.text[:200])


# --------------------------------------------------------------------------- #
# Scoring weights
# --------------------------------------------------------------------------- #
def test_weight_profiles(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]
    admin = state["admin_headers"]

    response = client.get("/api/weights", headers=recruiter)
    expect(response.status_code == 200 and response.json(), "list weights 200", response.text)

    response = client.get("/api/weights/default", headers=recruiter)
    expect(response.status_code == 200, "default weights 200", response.text)
    expect(
        abs(sum(response.json().values()) - 1.0) < 1e-6, "weights sum to 1", response.text
    )

    response = client.post(
        "/api/weights",
        json={
            "name": "skills-first",
            "description": "Weight required skills heavily",
            "weights": {
                "required_skills": 0.6,
                "experience": 0.15,
                "education": 0.1,
                "preferred_skills": 0.07,
                "semantic": 0.05,
                "certifications": 0.03,
            },
        },
        headers=admin,
    )
    expect(response.status_code == 201, "create weight profile 201", response.text)
    profile_id = response.json()["id"]

    response = client.post(
        "/api/weights",
        json={"name": "recruit-attempt", "weights": {"required_skills": 0.5}},
        headers=recruiter,
    )
    expect(response.status_code == 403, "recruiter cannot create weights", response.text)

    response = client.post(
        "/api/matches/analyze",
        json={"job_id": state["job_id"], "weight_profile": "skills-first", "force": True},
        headers=recruiter,
    )
    expect(response.status_code == 200, "analyze honours named profile", response.text)

    response = client.delete(f"/api/weights/{profile_id}", headers=admin)
    expect(response.status_code == 204, "delete weight profile 204", response.text)
    response = client.delete(f"/api/weights/{profile_id}", headers=admin)
    expect(response.status_code == 404, "delete missing profile 404", response.text)


# --------------------------------------------------------------------------- #
# Dashboard and assistant
# --------------------------------------------------------------------------- #
def test_dashboard(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.get("/api/dashboard", headers=recruiter)
    expect(response.status_code == 200, "dashboard 200", response.text)
    dashboard = response.json()
    expect(
        dashboard["stats"]["total_candidates"] == 3, "dashboard candidate count", response.text
    )
    expect(dashboard["stats"]["active_jobs"] >= 3, "dashboard job count", response.text)
    expect(bool(dashboard["score_distribution"]), "dashboard score buckets", response.text)
    expect(bool(dashboard["top_skills"]), "dashboard top skills", response.text)
    expect(
        bool(dashboard["most_missing_skills"]),
        "dashboard missing skills cover all scored candidates",
        response.text,
    )

    response = client.get("/api/dashboard", params={"job_id": state["job_id"]}, headers=recruiter)
    expect(response.status_code == 200, "dashboard filtered by job 200", response.text)


def test_assistant(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.get("/api/assistant/status", headers=recruiter)
    expect(response.status_code == 200, "assistant status 200", response.text)

    response = client.post(
        "/api/assistant/query",
        json={"message": "Who are the top 3 candidates for this job?", "job_id": state["job_id"]},
        headers=recruiter,
    )
    expect(response.status_code == 200, "assistant query 200", response.text)
    answer = response.json()
    expect(bool(answer["answer"]), "assistant answered", response.text)
    expect(bool(answer["intent"]), "assistant reports intent", response.text)
    expect(bool(answer["sources"]), "assistant cites sources", response.text)
    expect(
        "jordan.alvarez@example.com" not in answer["answer"],
        "assistant redacts pii",
        answer["answer"],
    )
    expect(bool(answer["conversation_id"]), "assistant returns a conversation id", response.text)

    response = client.post(
        "/api/assistant/query",
        json={
            "message": "And their experience?",
            "job_id": state["job_id"],
            "conversation_id": answer["conversation_id"],
        },
        headers=recruiter,
    )
    expect(response.status_code == 200, "assistant follow-up 200", response.text)


# --------------------------------------------------------------------------- #
# Admin operations
# --------------------------------------------------------------------------- #
def test_admin_user_management(client: TestClient, state: dict[str, object]) -> None:
    admin = state["admin_headers"]

    response = client.post(
        "/api/admin/users",
        json={
            "name": "Deleted Later",
            "email": "temp@example.com",
            "password": "TempUser1",
            "role": "recruiter",
        },
        headers=admin,
    )
    expect(response.status_code == 201, "admin creates user 201", response.text)
    temp_user_id = response.json()["id"]

    response = client.post(
        "/api/admin/users",
        json={"name": "Dup", "email": "temp@example.com", "password": "TempUser1"},
        headers=admin,
    )
    expect(response.status_code == 409, "admin duplicate user 409", response.text)

    response = client.delete(f"/api/admin/users/{temp_user_id}", headers=admin)
    expect(response.status_code == 204, "deactivate user 204", response.text)

    # The bootstrap admin is user 1; locking yourself out must be refused.
    response = client.delete("/api/admin/users/1", headers=admin)
    expect(response.status_code == 400, "cannot deactivate self 400", response.text)


def test_audit_trail(client: TestClient, state: dict[str, object]) -> None:
    admin = state["admin_headers"]

    response = client.get("/api/admin/audit-logs", headers=admin)
    expect(response.status_code == 200 and response.json(), "audit logs 200", response.text)
    actions = {row["action"] for row in response.json()}
    expect("auth.login" in actions, "audit captured login", str(actions))
    expect("match.analyze" in actions, "audit captured analysis", str(actions))

    response = client.get("/api/admin/audit-logs", params={"user_id": 1}, headers=admin)
    expect(response.status_code == 200, "audit filter by user 200", response.text)


def test_storage_and_retention(client: TestClient, state: dict[str, object]) -> None:
    admin = state["admin_headers"]

    response = client.get("/api/admin/storage", headers=admin)
    expect(
        response.status_code == 200 and response.json()["document_count"] >= 2,
        "storage info 200",
        response.text,
    )

    # Retention must not delete documents a record still points at, otherwise a
    # recruiter opens a profile and finds a missing resume.
    before = client.get(f"/api/candidates/{state['candidate_a']}", headers=admin).status_code
    response = client.post("/api/admin/retention/purge", headers=admin)
    expect(response.status_code == 200, "retention purge 200", response.text)
    after = client.get(f"/api/candidates/{state['candidate_a']}", headers=admin).status_code
    expect(before == 200 and after == 200, "retention kept referenced documents", response.text)

    response = client.get("/api/admin/roles", headers=admin)
    expect(
        response.status_code == 200 and "admin" in response.json()["roles"],
        "roles 200",
        response.text,
    )


# --------------------------------------------------------------------------- #
# Password change and logout
# --------------------------------------------------------------------------- #
def test_password_change_and_logout(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrongpass1", "new_password": "NewPassw0rd"},
        headers=recruiter,
    )
    expect(response.status_code == 400, "change-password wrong current 400", response.text)

    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "Recruiter1", "new_password": "weak"},
        headers=recruiter,
    )
    expect(response.status_code == 422, "change-password weak new 422", response.text)

    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "Recruiter1", "new_password": "NewPassw0rd"},
        headers=recruiter,
    )
    expect(response.status_code == 204, "change-password 204", response.text)

    response = client.post(
        "/api/auth/login", json={"email": "sam@example.com", "password": "NewPassw0rd"}
    )
    expect(response.status_code == 200, "login with new password", response.text)
    fresh = response.json()["tokens"]

    response = client.post(
        "/api/auth/logout",
        json={"refresh_token": fresh["refresh_token"]},
        headers=bearer(fresh),
    )
    expect(response.status_code == 204, "logout 204", response.text)

    response = client.post("/api/auth/refresh", json={"refresh_token": fresh["refresh_token"]})
    expect(response.status_code == 401, "refresh after logout is dead", response.text)

    # An empty body means "end every session", and must not be mistaken for the
    # access token being revoked as well.
    response = client.post("/api/auth/logout", headers=state["admin_headers"])
    expect(response.status_code == 204, "logout all sessions 204", response.text)
    response = client.post(
        "/api/auth/refresh", json={"refresh_token": state["rotated_refresh"]}
    )
    expect(response.status_code == 401, "logout-all killed every refresh token", response.text)


# --------------------------------------------------------------------------- #
# Deletion and PII scrubbing
# --------------------------------------------------------------------------- #
def test_deletion_paths(client: TestClient, state: dict[str, object]) -> None:
    recruiter = state["recruiter_headers"]

    response = client.get(f"/api/jobs/{state['job_id']}/candidates", headers=recruiter)
    expect(
        response.status_code == 200 and response.json()["candidate_count"] == 3,
        "job candidate count",
        response.text,
    )

    response = client.delete(f"/api/jobs/{state['parsed_job_id']}", headers=recruiter)
    expect(response.status_code == 204, "delete job 204", response.text)
    response = client.get(f"/api/jobs/{state['parsed_job_id']}", headers=recruiter)
    expect(response.status_code == 404, "deleted job gone", response.text)

    response = client.delete(f"/api/candidates/{state['manual_candidate_id']}", headers=recruiter)
    expect(response.status_code == 204, "delete candidate 204", response.text)
    response = client.get(f"/api/candidates/{state['manual_candidate_id']}", headers=recruiter)
    expect(response.status_code == 404, "deleted candidate gone", response.text)

    response = client.get("/api/candidates", headers=recruiter)
    expect(response.json()["total"] == 2, "candidate pool shrank", response.text)

    response = client.post(
        f"/api/admin/candidates/{state['candidate_b']}/purge", headers=state["admin_headers"]
    )
    expect(response.status_code == 204, "hard purge 204", response.text)
    response = client.get("/api/candidates", headers=recruiter)
    expect(response.json()["total"] == 1, "purged candidate gone", response.text)


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #
def test_repeated_failed_logins_are_answered(client: TestClient) -> None:
    codes = [
        client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "Whatever1"},
        ).status_code
        for _ in range(5)
    ]
    expect(
        all(code in (401, 429) for code in codes),
        "repeated logins still answered",
        str(codes),
    )