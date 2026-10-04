# Deployment

Two supported shapes: `docker compose` for a self-hosted deployment, and a
manual run of the two processes if you deploy to an existing platform. Both run
the same images and the same migrations.

> **Verification status.** The Dockerfiles, compose file and nginx config were
> written in an environment with no Docker daemon, so they have **not** been
> built or started here. The CI workflow builds both images and boots the API
> container on every push; treat the first `docker compose build` on your machine
> as the real check. Everything non-container (backend tests, migrations in both
> directions, ML pipeline, frontend build) has been executed locally.

## Compose

```bash
cp .env.example .env
# Edit .env: set SECRET_KEY and ADMIN_PASSWORD. Both are mandatory.
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

The app is on http://localhost:8080. Compose reads `.env` from the compose
file's own directory, which is why `--env-file` points back at the repository
root.

Three containers:

| Service | Image | Port | Notes |
| --- | --- | --- | --- |
| `db` | `postgres:16-alpine` | internal | Healthchecked; no host port unless you uncomment it |
| `api` | built from `docker/Dockerfile.backend` | internal 8000 | Runs migrations, then uvicorn as uid 10001 |
| `web` | built from `docker/Dockerfile.frontend` | 8080 | nginx: static SPA + `/api` proxy |

Startup is ordered by healthchecks, not by hope: `api` waits for `pg_isready`,
`web` waits for the API to report healthy. A cold `up` never serves an error
page.

```bash
docker compose --env-file .env -f docker/docker-compose.yml ps
docker compose --env-file .env -f docker/docker-compose.yml logs -f api
docker compose --env-file .env -f docker/docker-compose.yml down       # stop
docker compose --env-file .env -f docker/docker-compose.yml down -v    # stop and delete data
```

`down -v` deletes the PostgreSQL volume **and** the upload volume. Candidate
resumes live in `api_storage`; back it up before running it.

### What the images do

**API** (`docker/Dockerfile.backend`): a builder stage compiles wheels with a
compiler available, and a slim runtime stage installs from those wheels only. No
toolchain ships to production. It runs as uid 10001 with `/data/storage` owned
by that user, `tini` as PID 1 so `SIGTERM` reaches uvicorn for a clean shutdown,
and `libpq5` as the only runtime library. `sentence-transformers` and torch are
deliberately absent, which keeps the image small and the build offline.

**Web** (`docker/Dockerfile.frontend`): `npm ci` from the committed lockfile,
`npm run build`, then the static output is copied into
`nginxinc/nginx-unprivileged`, which runs as a non-root user on port 8080 with no
capabilities. The bundle is built with `VITE_API_URL` empty, so the SPA calls
its own origin and nginx proxies `/api` - no CORS, no API URL baked into
JavaScript.

`VITE_API_URL` is a build-time value. To point a bundle at a separate API host,
rebuild with `docker compose build --build-arg VITE_API_URL=https://api.example.com`.

### nginx

`docker/nginx.conf` sets `client_max_body_size 12m` (above the 10 MB upload cap
so the API produces the error message, not nginx), disables request and response
buffering for `/api` so uploads and exports stream, never caches API responses,
gives hashed assets a one-year `max-age` while forcing `index.html` to
revalidate, and sends `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy:
no-referrer`, `Permissions-Policy` and a CSP with `script-src 'self'`.

The upstream name is resolved through Docker's DNS every 10 seconds rather than
pinned at startup, so recreating the API container does not leave nginx proxying
to a dead IP.

TLS is not configured in the image. Terminate it at a load balancer, an ingress
controller, or add a certificate to nginx. The API sets HSTS whenever
`ENVIRONMENT` is not `development`.

## Configuration

Every setting comes from the environment; `.env.example` is the full list with
comments. The ones that matter in production:

```dotenv
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=<48+ random bytes>            # python -c "import secrets;print(secrets.token_urlsafe(48))"
DATABASE_URL=postgresql+asyncpg://…      # compose builds this from POSTGRES_*
ADMIN_EMAIL=you@example.com
ADMIN_PASSWORD=<set once, then remove>
CORS_ORIGINS=[]                          # same-origin via nginx needs none
EMBEDDING_PROVIDER=hashing               # or openai / sentence_transformers
LLM_PROVIDER=none
RETENTION_DAYS=365
RATE_LIMIT_PER_MINUTE=60
BCRYPT_ROUNDS=12
```

Notes:

- `ENVIRONMENT=production` disables auto-creating the schema, so Alembic owns it,
  and returns generic error messages instead of tracebacks. Reference data (skill
  taxonomy, default weights) is still seeded in every environment, because
  without them nothing works.
- `/docs`, `/redoc` and `/openapi.json` are served in `development` and `test`
  and **not** in `staging` or `production`: an exposed schema is a complete
  inventory of routes and parameters. Set `EXPOSE_API_DOCS=true` to expose them
  anyway. See `docs_enabled()` in `backend/app/main.py`.
- At startup the app logs an error if `SECRET_KEY` or `ADMIN_PASSWORD` are still
  the example values. That message belongs in your deploy-log review.
- `CORS_ORIGINS=[]` is correct behind nginx: the browser only ever talks to one
  origin, so no cross-origin allowance is needed. Set it only if the SPA is
  served from a different host than the API, and list exact origins - never `*`
  with credentials.
- `ADMIN_PASSWORD` creates the first administrator when the user table is empty.
  Remove it from the environment afterwards; the account stays, the secret does
  not have to linger.
- `BCRYPT_ROUNDS` trades login latency for resistance to offline cracking.
  Existing hashes are upgraded on the next successful login when you raise it.

## Migrations

Alembic owns the schema. The API container runs `alembic upgrade head` before
uvicorn starts, which is idempotent, so every replica may run it.

```bash
# Locally
cd backend && python -m alembic upgrade head

# Check nothing is missing
python -m alembic check

# Roll back one revision, then re-apply
python -m alembic downgrade -1 && python -m alembic upgrade head

# Inspect
python -m alembic current
python -m alembic history --verbose
```

For a zero-downtime deploy where migrations must not run from every replica,
remove the migration step from the command and run it once as a release job:

```bash
docker compose --env-file .env -f docker/docker-compose.yml run --rm api \
  sh -c "cd /app/backend && python -m alembic upgrade head"
```

Reviewed in this repository: upgrade, `check`, downgrade and re-upgrade all pass
on SQLite, and the container startup path runs the same `upgrade head`.

## Backups and restore

Two things hold state: the PostgreSQL volume and the upload volume.

```bash
# Logical backup
docker compose --env-file .env -f docker/docker-compose.yml exec -T db \
  pg_dump -U postgres ai_resume | gzip > ai_resume-$(date +%F).sql.gz

# Files
docker run --rm -v talentmatch_api_storage:/data -v "$PWD:/out" alpine \
  tar czf /out/storage-$(date +%F).tar.gz -C /data .
```

Restore the database dump, restore the archive to the same volume name, then
start the stack. Backups are only real once you have restored one into a scratch
environment.

For retention, schedule the idempotent purge rather than deleting rows by hand:

```bash
curl -X POST http://localhost:8080/api/admin/retention/purge -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Scaling

The API is stateless except for its rate limiter, which is per process and
in-memory. Running N replicas therefore multiplies the effective rate limit by N
and, if you switch on `EMBEDDING_PROVIDER=openai`, shares nothing between them.
Start by scaling the single container:

- More CPU helps scoring and parsing, which are CPU-bound.
- The database is the first bottleneck for large candidate pools; the ranking
  query orders and paginates in SQL for exactly that reason.
- Frontend scaling is free: `nginx` serves static files and can be replaced by a
  CDN.

Replace the in-process limiter with Redis, and the container with an orchestrator,
only when a measured limit forces you to.

## Environment differences

| | Development | Compose |
| --- | --- | --- |
| Database | SQLite file under `storage/` | PostgreSQL 16 |
| Schema | Auto-created in `development` only | Alembic at container start |
| API docs | `/docs` fully interactive | Not served (`EXPOSE_API_DOCS=true` to enable) |
| Embeddings | `sentence_transformers` in `.env.example` | `hashing`, so the build needs no model download |
| Frontend | Vite dev server on 5173 with a proxy | Static bundle behind nginx on 8080 |

That difference in embedding provider is deliberate but easy to miss: a
development machine may be ranking with MiniLM while the deployed container ranks
with hashed n-grams. Scores are not comparable across the two. Set
`EMBEDDING_PROVIDER` to the same value in both, then re-run matching.

## Pre-flight checklist

```bash
# Config sanity
grep -E "^(SECRET_KEY|ENVIRONMENT|DATABASE_URL)" .env

# Health
curl -fsS http://localhost:8080/api/health

# Tests, the same commands CI runs
cd backend && python -m pytest && cd ..
python -m pytest ml
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build && cd ..
```

Then verify by hand: sign in as the bootstrap admin, create a job from a pasted
description, upload a resume, run matching, read the evidence for the top
candidate, export the ranking, and confirm the audit log recorded it.