# API reference

Base path `/api`. Interactive documentation is served at `/docs` (Swagger UI),
`/redoc`, and the raw schema at `/openapi.json`. 42 paths, 51 operations.

## Conventions

- **Media type**: JSON in and out, except resume and job-description uploads
  (`multipart/form-data`) and ranking export (`text/csv` or `application/json`).
- **IDs**: integers, not UUIDs. A user, job, candidate and match id are unrelated
  numbers.
- **Timestamps**: ISO 8601, UTC.
- **Organisation scoping**: every authenticated query is filtered to the caller's
  organisation. Another tenant's record returns 404, never 403, so the API never
  confirms that a record exists.

### Pagination

List endpoints take `?page=` (1-based, default 1) and `?page_size=` (default 20,
max 100) and answer with the same envelope:

```json
{ "total": 137, "page": 1, "page_size": 20, "pages": 7, "results": [ … ] }
```

`pages` is always at least 1, so the UI can render a pager without special cases.

### Errors

Every failure carries a `request_id`, which is also returned in the
`X-Request-ID` response header and written to the server log. A screenshot of an
error is enough to find the trace.

```json
{ "detail": "No such job.", "request_id": "0f3c…" }
```

Validation failures add a field-level list:

```json
{
  "detail": "Request validation failed.",
  "errors": [{ "loc": ["body", "weights"], "msg": "Weights must sum to 1.0, got 0.9000" }],
  "request_id": "0f3c…"
}
```

## Roles

Three roles exist: `admin`, `recruiter`, `hiring_manager`. There are two
effective tiers.

| Tier | Roles | Can |
| --- | --- | --- |
| Data | `recruiter`, `hiring_manager`, `admin` | Everything involving jobs, candidates, matching, dashboard, assistant, skills |
| Administration | `admin` | Plus user management, audit logs, storage stats, retention purge, taxonomy sync, weight profiles |

`hiring_manager` sits with `recruiter` deliberately: they run and review matches
for their roles. Configuration that changes everyone's results - weights, users,
retention - stays with an admin. This is enforced in
`backend/app/api/deps.py` (`RecruiterUser`, `AdminUser`) and locked in by
`test_hiring_manager_reaches_candidate_data`.

Self-registration always creates a `recruiter`. A caller-supplied `role` in the
registration body is ignored and recorded in the audit log, so a probe shows up
in the trail without being honoured.

## Authentication

```
POST /api/auth/register        -> 201 { user, tokens }   (always role: recruiter)
POST /api/auth/login           -> 200 { user, tokens }
POST /api/auth/refresh         -> 200 { access_token, refresh_token, token_type, expires_in }
POST /api/auth/logout          -> 200 (revokes the presented refresh token)
GET  /api/auth/me              -> 200 UserRead
POST /api/auth/change-password -> 204 (revokes every refresh token for the user)
POST /api/auth/bootstrap-admin -> 201 UserRead (only while the user table is empty)
```

`tokens`:

```json
{
  "access_token": "eyJ…",
  "refresh_token": "eyJ…",
  "token_type": "bearer",
  "expires_in": 1800
}
```

Access tokens are short-lived (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 30).
Refresh tokens live `REFRESH_TOKEN_DAYS` (default 7), are **hashed** in the
database, and rotate on use: `POST /api/auth/refresh` returns a new pair and
invalidates the old refresh token. `logout` and `change-password` revoke
server-side, so a leaked refresh token dies with the session.

The browser client (`frontend/src/api/client.ts`) refreshes proactively on a 401
and retries the original request once.

## Endpoints

Minimum role is shown as *data* or *admin*, or *public*.

### Health

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/health` | public | Liveness plus database reachability. Also the container healthcheck. |

### Jobs

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/jobs` | data | Paginated; filters `search`, `is_active` (default true) |
| POST | `/api/jobs` | data | Title, description, requirements |
| GET | `/api/jobs/{job_id}` | data | Includes candidate counts |
| PATCH | `/api/jobs/{job_id}` | data | Partial update |
| DELETE | `/api/jobs/{job_id}` | data | Cascades to the job's match results |
| POST | `/api/jobs/parse` | data | Paste a description, get structured requirements back **for review** |
| POST | `/api/jobs/upload` | data | `multipart/form-data`, `.pdf` / `.docx` / `.txt` |
| GET | `/api/jobs/{job_id}/requirements` | data | The exact requirements used for scoring |
| GET | `/api/jobs/{job_id}/candidates` | data | Candidates attached to the job, ranked |

`POST /api/jobs/parse` does not persist anything on its own. Extracted
requirements are a proposal a human confirms; auto-creating a role from scraped
text would put unverified content into every downstream score.

### Candidates

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/candidates` | data | Paginated search; see filters below |
| POST | `/api/candidates` | data | Manual entry |
| GET | `/api/candidates/{id}` | data | Profile, skills, certifications, status |
| PATCH | `/api/candidates/{id}` | data | Replaces the skill set by name when `skills` is supplied |
| DELETE | `/api/candidates/{id}` | data | Soft delete, cascades to match results |
| POST | `/api/candidates/upload` | data | One file or a batch; returns per-file outcomes |
| GET | `/api/candidates/{id}/resume` | data | Streams the stored file after an ownership check |
| POST | `/api/candidates/{id}/resume` | data | Replace the file and reparse |
| GET | `/api/candidates/{id}/sections` | data | Parsed summary, experience, education, skills |
| POST | `/api/candidates/{id}/reparse` | data | Re-run parsing on the stored file |
| POST | `/api/candidates/{id}/shortlist` | data | `?shortlisted=true|false`, returns the updated candidate |

Candidate search filters, all ANDed: `search` (name, email, title, location),
`location`, `current_title`, `min_experience`, `max_experience`, `education`,
`certification`, `shortlisted_only`, and repeatable `skills` which requires
**all** listed skills.

Shortlisting is a human decision. The endpoint never infers it from a score, and
nothing in the pipeline sets it automatically.

Resume size is capped by `MAX_UPLOAD_SIZE_MB` (default 10) and extensions by
`ALLOWED_RESUME_EXTENSIONS`, both enforced server-side.

### Matching and ranking

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| POST | `/api/matches/analyze` | data | Score candidates against a job; returns the top of the table |
| POST | `/api/matches/compare` | data | 2-10 candidates, component by component |
| POST | `/api/matches/export` | data | `format: "csv" | "json"`, streamed as an attachment |
| GET | `/api/matches/{match_id}` | data | Raw evidence rows and the weights used |
| GET | `/api/jobs/{job_id}/ranking` | data | Paginated ranking, `sort_by`, `shortlisted_only` |
| GET | `/api/weights` | data | Saved weight profiles |
| GET | `/api/weights/default` | data | The built-in profile |
| POST | `/api/weights` | **admin** | Create or replace a profile |
| DELETE | `/api/weights/{profile_id}` | **admin** | Delete a custom profile |

`POST /api/matches/analyze` body:

```json
{ "job_id": 1, "candidate_ids": [2, 3], "limit": 100, "weight_profile": "Backend-heavy", "force": false }
```

It answers with `{ job_id, analyzed, skipped, failed, duration_ms, top_candidates }`,
so a partial failure is visible instead of silently producing a short ranking.

`GET /api/jobs/{job_id}/ranking?page=1&page_size=20&sort_by=overall`:

```json
{
  "job_id": 1,
  "job_title": "Senior Backend Engineer",
  "total_candidates": 137,
  "sort_by": "overall",
  "weights": {
    "required_skills": 0.4, "experience": 0.2, "education": 0.15,
    "preferred_skills": 0.1, "semantic": 0.1, "certifications": 0.05
  },
  "results": [
    {
      "rank": 1,
      "candidate": { "id": 42, "full_name": "…", "current_title": "…", "total_experience_years": 7.0 },
      "match": {
        "id": 900,
        "job_id": 1,
        "candidate_id": 42,
        "required_skills_score": 0.857,
        "experience_score": 1.0,
        "education_score": 1.0,
        "preferred_skills_score": 0.5,
        "semantic_score": 0.61,
        "certifications_score": 1.0,
        "overall_score": 87.4,
        "weights": { "required_skills": 0.4, "…": 0.0 },
        "explanation": {
          "summary": "…",
          "strong_matches": ["Python", "FastAPI", "PostgreSQL"],
          "preferred_matches": ["Docker"],
          "missing_required": ["Kubernetes"],
          "weak_signals": [],
          "experience_note": "7 years against 5 required",
          "education_note": "…",
          "certification_note": null,
          "semantic_note": "…",
          "recommendation": "…"
        },
        "evidence": [
          { "component": "required_skills", "label": "…", "detail": "…",
            "status": "met", "weight": 0.4, "snippet": "…" }
        ],
        "created_at": "2026-01-01T09:00:00Z"
      }
    }
  ],
  "disclaimer": "Scores are decision-support signals …"
}
```

Every response containing a score also carries the disclaimer and the evidence
rows. Ordering and pagination happen in SQL with the candidate id as a
tie-breaker, so equal scores cannot drop or duplicate a candidate across a page
boundary.

Weights must sum to 1.0 (`abs(total - 1.0) <= 1e-3`); anything else is rejected
rather than silently normalised, because a quietly rescaled profile silently
changes everyone's ranking.

### Skills taxonomy

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/skills` | data | Catalogue, searchable, categorised |
| GET | `/api/skills/count` | data | Catalogue size |
| POST | `/api/skills/sync` | **admin** | Fold newly parsed free-text skills into the catalogue |

### Dashboard and assistant

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/dashboard` | data | `stats`, candidates-per-job, score distribution, top and most-missing skills, recent candidates |
| POST | `/api/assistant/query` | data | Natural-language question over this tenant's data |
| GET | `/api/assistant/status` | data | Whether an LLM provider is configured |

With `LLM_PROVIDER=none` (the default) the assistant answers from structured
queries and states that it is not using a model, rather than implying otherwise.
It will not return a recommendation to reject a candidate.

### Administration

| Method | Path | Role | Notes |
| --- | --- | --- | --- |
| GET | `/api/admin/users` | **admin** | Paginated via `limit` / `offset` |
| POST | `/api/admin/users` | **admin** | Create a user with an explicit `role` |
| DELETE | `/api/admin/users/{user_id}` | **admin** | Delete a user |
| GET | `/api/admin/roles` | **admin** | The roles the API understands |
| GET | `/api/admin/audit-logs` | **admin** | Paginated audit trail |
| GET | `/api/admin/storage` | **admin** | Upload counts and disk usage |
| POST | `/api/admin/retention/purge` | **admin** | Delete candidates older than `RETENTION_DAYS` |
| POST | `/api/admin/candidates/{id}/purge` | **admin** | Hard-delete one candidate and its files |

Purge removes rows **and** stored files and writes an audit entry naming what was
deleted. Retention purge is idempotent, so it is safe to schedule.

## Status codes

| Code | Meaning here |
| --- | --- |
| 200 / 201 | Success; 201 on creation |
| 204 | Success with no body (delete, password change) |
| 400 | Well-formed request that breaks a business rule |
| 401 | Missing, expired, malformed or revoked token; inactive account |
| 403 | Authenticated, wrong role |
| 404 | Not found, or hidden by organisation scoping |
| 409 | Conflict, e.g. email already registered |
| 413 | Upload exceeds `MAX_UPLOAD_SIZE_MB` |
| 415 | Unsupported file extension |
| 422 | Schema validation failure, with per-field `errors` |
| 429 | Rate limit exceeded (`RATE_LIMIT_PER_MINUTE`) |
| 500 | Unhandled error; details are logged, never returned in production |