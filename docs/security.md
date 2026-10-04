# Security

What this service actually protects against, how, and where the gaps are. Every
claim below points at code; if a control is aspirational it is listed under
[Known gaps](#known-gaps) instead.

## Threat model

The system stores resumes: names, email addresses, phone numbers, employment
history, education, links. The assets worth defending are, in order:

1. **Candidate personal data.** Disclosure is the worst outcome: it is
   identifiable, sensitive, and in many jurisdictions subject to a right to
   erasure.
2. **Tenant isolation.** One recruiter must never see another organisation's
   candidates, even though the schema is shared.
3. **Account takeover.** Sessions are short-lived and revocable precisely so a
   stolen credential has a bounded lifetime.
4. **Ranking integrity.** If scores can be silently changed, every downstream
   decision is unaccountable.

Out of scope: physical host security, TLS termination (handled by the edge),
and the security of third-party hosted model providers.

## Authentication and sessions

| Control | Implementation |
| --- | --- |
| Password hashing | bcrypt, cost from `BCRYPT_ROUNDS` (default 12). `app/core/security.py` |
| Long passwords | Rejected above 72 bytes rather than silently truncated, since bcrypt ignores the tail |
| Access tokens | JWT, HS256, `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30). `app/core/security.py` |
| Refresh tokens | JWT with a `type` claim; decoding an access token as a refresh token (or the reverse) fails |
| Refresh token storage | **SHA-256 fingerprint only.** `refresh_tokens.token` holds a digest, so a database dump yields no usable session. `app/core/security.py:token_fingerprint` |
| Refresh rotation | Every refresh revokes the presented token and issues a new pair; replay of an old token returns 401. `app/services/auth.py` |
| Logout / password change | Revokes refresh tokens server-side, so the client cannot keep an old session alive |
| Inactive accounts | Re-checked on every request, not just at token issue. `app/api/deps.py` |
| Timing | bcrypt verification is constant-time in the library; failed logins return one generic message so the response does not reveal whether an email exists |

`test_refresh_tokens_are_not_stored_in_replayable_form` reads the table directly
to assert the digest, and `test_refresh_rotates_and_invalidates` covers
rotation. If you change either behaviour, the tests fail rather than the
docstring.

## Authorization

Three roles: `admin`, `recruiter`, `hiring_manager`. Enforcement lives in two
dependencies in `app/api/deps.py`, and nothing else guards a route:

- `AdminUser` - user management, audit logs, storage stats, retention purge,
  taxonomy sync, weight profiles.
- `RecruiterUser` - `admin`, `recruiter`, `hiring_manager`: jobs, candidates,
  matching, dashboard, assistant, skills.

An insufficient role returns 403 with the list of roles that would be allowed.
A missing or invalid token returns 401. A record outside the caller's
organisation returns **404, not 403**: a 403 would confirm the record exists,
which is a small information leak on a multi-tenant system.

Self-registration cannot escalate. `POST /api/auth/register` accepts a `role`
field and ignores it, always creating a `recruiter`; the requested role is
recorded in the audit log so a probe is visible. Role assignment lives behind
`POST /api/admin/users`.

## Tenant isolation

Every authenticated query filters on the caller's organisation through the
service layer, not per route. Adding a new endpoint without that filter is the
main way to break isolation, which is why the safe pattern is to go through the
existing service functions instead of writing a new `select()` in a router.

## Input handling

| Surface | Control |
| --- | --- |
| JSON bodies | Pydantic 2 schemas with explicit bounds (`ge`, `le`, `max_length`) |
| Query parameters | `Annotated` with `Query(ge=…, le=…)`, e.g. `page_size` is capped at 100 |
| Uploads | Extension whitelist (`ALLOWED_RESUME_EXTENSIONS`), size cap (`MAX_UPLOAD_SIZE_MB`), empty-file rejection |
| Upload paths | Unguessable `secrets.token_hex(16)` filenames under a month-sharded directory; the stored name never derives from user input |
| Path traversal | `_resolve()` rejects any resolved path outside the storage root. `app/utils/storage.py` |
| Partial writes | Uploads are written to `.part` and `os.replace`d, so a reader never sees a half file |
| Ranking weights | Must sum to 1.0 within 1e-3; rejected rather than normalised, so nobody silently changes everyone's results |
| Errors | Uniform `{detail, request_id}` body. Production returns a generic message for unhandled errors and logs the trace |

## Transport and headers

- **CORS**: `CORSMiddleware` with an explicit `CORS_ORIGINS` allowlist and
  credentials enabled. Never `*` with credentials.
- **Security headers**: applied by the app (`app/main.py:security_headers`) so
  they hold without a reverse proxy: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy: same-origin`. HSTS is added outside
  development.
- **SPA headers**: nginx additionally sends a CSP with `script-src 'self'` and
  `frame-ancestors 'none'`, a one-year `max-age` for hashed assets with
  `index.html` forced to revalidate, and `no-store`-equivalent behaviour for API
  responses. The API re-resolves the upstream name every 10 seconds, so
  recreating the API container does not require an nginx reload. See
  `docker/nginx.conf`.
- **TLS**: terminated at the edge (nginx, a load balancer, or the ingress). The
  app sets no cookies, so there is no cookie flag to configure; the bearer token
  lives in browser storage, which is why the CSP matters.

## Rate limiting

`RateLimitMiddleware` is a fixed-window in-process counter, keyed by client IP,
default 60 requests/minute (`RATE_LIMIT_PER_MINUTE`). Health checks and the docs
endpoints are exempt. Two deliberate properties:

- **It fails open.** A limiter bug must not become an outage.
- **It is per process.** Behind multiple replicas each process counts
  independently, so the effective limit is `limit × replicas`. The class
  documents the same two-method interface a Redis-backed implementation would
  need; swapping it is a contained change.

Rate limiting blunts abuse and scraping. It is not an authorisation control.

## Secrets

- `SECRET_KEY` signs every JWT. It must be replaced before any real deployment:
  `python -c "import secrets;print(secrets.token_urlsafe(48))"`.
- Compose refuses to start without `SECRET_KEY` and `ADMIN_PASSWORD`
  (`:?` in the variable list), so a default key cannot reach production by
  accident.
- `ADMIN_PASSWORD` creates the first administrator when the user table is empty.
  Clear it from the environment after the first successful sign-in.
- `OPENAI_API_KEY` is only read when `LLM_PROVIDER=openai` or the OpenAI
  embedding provider is selected. Nothing else reads it.
- `.env` is gitignored, and the Docker build context excludes it
  (`.dockerignore`), so a secret cannot enter an image layer through a `COPY`.

## Data handling

- Uploaded resumes live under `STORAGE_DIR` and are served only through
  `GET /api/candidates/{id}/resume` after an ownership check. There is no static
  mount, so knowing a filename is not enough.
- Deleting a candidate is a soft delete that cascades to match results; a purge
  additionally removes the stored file.
- `POST /api/admin/retention/purge` removes candidates older than
  `RETENTION_DAYS` (default 365) together with their files, and is idempotent so
  it can be scheduled.
- Every purge, weight change, user change, export, login, logout and ranking view
  is written to `audit_logs` with actor, IP and user agent.

## Fairness and protected attributes

Ranking features never read `gender`, `birth_year` or any other protected
attribute. This is enforced by tests that fail if a protected field reaches the
feature matrix, not by convention - see `ml/tests/test_fairness.py` and
[responsible-ai.md](responsible-ai.md).

## Known gaps

Stated plainly, because a security document that claims completeness is worse
than none:

1. **No file content sniffing.** Uploads are validated by extension and size.
   A file renamed to `.pdf` is accepted and then fails to parse. Adding magic-byte
   checks for `%PDF`, the ZIP header of `.docx` and plain-text detection would
   close this.
2. **Tokens in browser storage.** The bearer token is kept in `localStorage`,
   which is readable by any script on the origin. The CSP mitigates this; an
   httpOnly, SameSite cookie plus CSRF protection would remove the exposure
   class entirely, at the cost of the current stateless API shape.
3. **Single `SECRET_KEY`.** Rotating it invalidates every session at once, and
   there is no key id or grace window for zero-downtime rotation.
4. **Rate limiting is per process** and in-memory, so it resets on restart and
   multiplies across replicas.
5. **No malware scanning** of uploaded documents.
6. **Audit logs are append-only by convention.** Nothing prevents an account
   with database access from editing them; a real deployment would ship them to
   append-only storage.
7. **No 2FA, no SSO, no IP allowlist.** Fine for an internal tool, not for a
   public service.
8. **`sentence_transformers` and OpenAI providers send resume text to a model.**
   With `EMBEDDING_PROVIDER=hashing` (the Docker default) nothing leaves the
   host.
9. **Five high-severity advisories remain in the frontend dev toolchain**
   (`tailwindcss` → `chokidar` / `fast-glob` / `micromatch` / `braces`). They are
   build-time only and reach no browser, so `npm audit --omit=dev` is clean and
   CI enforces that. The Tailwind 4 upgrade that would clear them is a CSS and
   config migration with a real chance of visual regressions, so it is tracked
   rather than forced.

## Reporting

Open an issue describing the vulnerability, the affected version and how to
reproduce it. Do not include candidate data or real tokens in the report.