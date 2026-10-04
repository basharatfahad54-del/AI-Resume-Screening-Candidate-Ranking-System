# Architecture

## System shape

```
                    ┌──────────────────────────────────────────┐
   Browser ────────▶│  frontend/  React 19 + Vite SPA         │
   (one origin)     │  nginx in Docker: static assets + /api   │
                    └────────────────────┬─────────────────────┘
                                         │ HTTP, same origin, /api/*
                    ┌────────────────────▼─────────────────────┐
                    │  backend/  FastAPI (uvicorn, /api)       │
                    │                                          │
                    │   api/       routers + dependency graph  │
                    │   services/  business rules, transactions│
                    │   ml/        embeddings + scoring engine  │
                    │   models/    SQLAlchemy ORM              │
                    │   core/      config, security, errors     │
                    └────────────────────┬─────────────────────┘
                                         │ async SQLAlchemy
                    ┌────────────────────▼─────────────────────┐
                    │  PostgreSQL 16 (Docker)  or  SQLite file  │
                    └──────────────────────────────────────────┘
```

Uploads (resumes, job descriptions) are stored on a volume under `STORAGE_DIR`
and referenced from the database. They are never exposed by a static file mount:
every read goes through an endpoint that checks ownership.

The `ml/` package is deliberately outside this diagram. It is an offline
experiment that imports `backend/app/ml` so features cannot drift, and it never
runs on the request path.

## Request path

A typical ranking request:

1. `GET /api/jobs/{id}/ranking` resolves the job, loads the candidates already
   attached to it, and fetches the active weight profile.
2. `app/ml/embeddings.py` embeds the job requirements once and each candidate's
   resume once (cached per request).
3. `app/ml/scoring.py` computes six components per pair, multiplies by weights,
   sums, and clamps to `[0, 100]`.
4. Candidates are sorted, reasons are attached, and the response includes the
   component breakdown so the UI can show *why* a candidate scored what they did.
5. The list endpoint paginates; the export endpoint streams CSV.

Scoring is CPU-bound pure Python over small vectors. At the tested scale
(hundreds of candidates per job) this is comfortably sub-second, which is why
there is no model server, no queue and no caching layer in front of it. A
separate inference service would add latency, a failure mode and an audit gap for
no measurable gain.

## Backend layers

| Layer | Responsibility | Must not |
| --- | --- | --- |
| `api/` | HTTP shapes, status codes, auth/role dependencies | contain business rules |
| `services/` | transactions, invariants, cross-entity rules | import FastAPI request objects |
| `models/` | schema, relationships, cascades | perform I/O beyond lazy loads |
| `core/` | settings, JWT, password hashing, errors, rate limiting | import feature code |
| `ml/` | embeddings, scoring | touch the database or HTTP |

The dependency direction is strictly downward. The `ml` layer is the only place
that knows how scoring works, which is what lets `ml/src/talentmatch_ml` reuse it
without copying the logic and inheriting a second, drifting implementation.

## Data model

Principal entities:

- **Organisation** - tenant boundary. Every authenticated query is scoped to it.
- **User** - belongs to an organisation, has a role (`admin`, `recruiter`,
  `hiring_manager`, `viewer`) and an `is_active` flag.
- **Job** - role description with requirements (required/preferred skills,
  minimum experience, education level, certifications).
- **Candidate** - person record attached to an organisation, with parsed resume
  sections and skills. Optionally linked to `Job` through `JobCandidate`.
- **JobCandidate** - the scored pair: `score`, `rank`, `reasons`, `shortlisted`.
- **WeightProfile** - named, validated set of scoring weights summing to 1.0.
- **AuditLog** - actor, action, target, metadata. Append-only from the app's
  perspective.
- **RefreshToken** - hashed token, expiry, revocation flag.

Deletes cascade: removing a job removes its pairs; removing a candidate removes
its pairs. Removing an organisation is a purge, which is why the admin purge
endpoint exists and is audited.

## Ranking engine

`backend/app/ml/scoring.py` is the production scorer. For one (job, candidate)
pair:

| Component | Weight | Signal |
| --- | --- | --- |
| Required skills | 0.40 | fraction of required skills matched; partial credit above cosine 0.45 |
| Experience | 0.20 | years against the requirement, saturating at the requirement |
| Education | 0.15 | highest level against the requirement, ordinal |
| Preferred skills | 0.10 | fraction matched |
| Semantic | 0.10 | cosine similarity of embedded texts |
| Certifications | 0.05 | fraction of required certifications held |

Design choices worth knowing:

- **Additive and inspectable.** Each component is returned to the client. No
  hidden feature, no opaque total.
- **Weights are data.** Recruiters tune them per profile; the API rejects a
  profile whose weights do not sum to 1.0, so scores stay comparable.
- **Skill matching is name-based with a semantic fallback.** Declared skills
  match exactly (normalised), so "React.js" and "React" are the same skill.
  Unmatched required skills get partial credit when the whole resume is close
  to that skill, which recovers experience that was never declared as a skill.
- **Deterministic.** The same inputs always produce the same score, which is
  what makes audit logs and offline evaluation meaningful.

## Embeddings

`EMBEDDING_PROVIDER` selects one of three implementations behind one interface:

| Provider | Behaviour | Use |
| --- | --- | --- |
| `hashing` | Deterministic hashed n-grams, 384 dims, no network | Offline, tests, CI, default in Docker |
| `sentence_transformers` | MiniLM sentence embeddings | Local high-quality semantics |
| `openai` | Hosted embeddings API | Best quality, requires a key and data leaves the network |

All providers are normalised to unit length, so cosine similarity is a dot
product everywhere else in the codebase. The default is `hashing` in Docker
specifically to keep images small and builds hermetic; `sentence_transformers`
pulls in roughly 2 GB of torch.

Switching providers changes vectors, so **changing it invalidates cached scores**.
Recompute rankings for affected jobs after a switch.

## Frontend

- **Routing**: React Router 7, `ProtectedRoute` plus `RoleRoute` guards, layout
  route with an authenticated nav shell.
- **Server state**: TanStack Query. Query keys are centralised so mutations can
  invalidate exactly what they changed.
- **Forms**: React Hook Form + Zod schemas that mirror the backend validation.
- **Styling**: Tailwind. Small presentational helpers live in
  `src/lib/format.ts`; `src/components/Score.tsx` exports components only.
- **API access**: one typed client in `src/api/client.ts`. `VITE_API_URL` empty
  means same origin, which is what the nginx deployment uses.
- **Tests**: Vitest with jsdom for `App` routing, `ErrorBoundary` and
  `JobDetailPage` flows.

## Offline ML pipeline

```
synthetic corpus ──▶ features ──▶ grouped CV ──▶ paired bootstrap ──▶ report.md
   (dataset.py)     (features.py)   (train.py)      (metrics.py)      (report.py)
                          │
                          └── reuses backend/app/ml via backend_bridge.py
```

Cross-validation is grouped by job, so no candidate is scored by a model that saw
its own job during training. Ranking metrics use paired bootstrap over jobs, and
the report states a deployment decision rather than a leaderboard. See
[ml/README.md](../ml/README.md) for the experiment and its result.

## Deployment shape

Three containers: PostgreSQL, the API (non-root, uid 10001, migrations run at
startup), and nginx serving the static SPA with a `/api` reverse proxy. Compose
waits for the database healthcheck before starting the API and for the API
healthcheck before starting the web container, so a cold start never serves an
error page. Details in [deployment.md](deployment.md).

## Deliberate non-goals

- No message queue or worker tier: nothing in the product needs deferred work.
- No model server: see the scoring rationale above.
- No microservices: one API process with clear internal layers is easier to
  reason about and to audit.
- No automatic rejection: out of scope by policy, not by omission.