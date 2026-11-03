# KampusPing Backend

A production-ready FastAPI starter: JWT authentication with rotating refresh tokens, role-based
access control, a layered module structure, async SQLAlchemy 2, Alembic migrations, and a test
suite that covers the security-sensitive paths.

**Stack:** Python 3.11+, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, PostgreSQL
(MySQL / SingleStore / SQLite supported), pytest.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Pick a database in `.env`. For a zero-setup try, use SQLite:

```env
DATABASE_ENGINE=sqlite
DATABASE_URL=sqlite+aiosqlite:///./dev.db
```

Then:

```bash
alembic upgrade head                 # create tables
python -m app.cli seed               # sync permissions + system roles
python -m app.cli create-admin       # uses INITIAL_ADMIN_* from .env or prompts
uvicorn app.main:app --reload        # http://localhost:8000/docs
```

Sign in at `POST /api/v1/auth/login` and send the returned token as `Authorization: Bearer ...`.

## Project layout

```text
app/
├── main.py              # app factory: CORS, middleware, error handlers, /health, /ready
├── cli.py               # seed, create-admin, purge-tokens
├── api/
│   ├── deps.py          # DbSession, CurrentUser, require_permissions(...)
│   └── v1/router.py     # registers module routers under /api/v1
├── core/                # config, security (JWT/Argon2), permissions catalog, errors, logging
├── db/                  # Base + AuditedModel, async session, seed, migration helpers
├── modules/
│   ├── auth/            # login, refresh, logout, me, change-password
│   ├── users/           # user management
│   └── roles/           # roles and the permission catalog
└── shared/              # pagination contract, BaseRepository (soft delete), utils
alembic/                 # migrations
deploy/                  # PM2 + Nginx
tests/
```

Every business module follows the same flow, and only the router knows about HTTP:

```text
router.py  ->  schema.py  ->  service.py  ->  repository.py  ->  model.py
(HTTP)         (validation)   (rules, commit)  (queries)         (tables)
```

Start a new module by copying `app/modules/roles/` ([guide](docs/adding-a-module.md)).

## Conventions

| Topic          | Rule                                                                            |
| -------------- | ------------------------------------------------------------------------------- |
| Errors         | Always `{"error": {"code", "message", "details"}}`; raise `AppException`        |
| Success        | Plain resource or `{"items", "total", "page", "page_size"}`; no wrapper         |
| Lists          | `page` (1-based), `page_size` (≤ 100), `search`, `sort_by`, `sort_order`        |
| Deletes        | Soft delete (`deleted_at`, `deleted_by`); repositories filter it out            |
| Audit          | `created_by` / `updated_by` / `deleted_by` come from the signed-in user         |
| Timestamps     | Stored and returned in UTC, timezone-aware                                      |
| Transactions   | Services call `commit()`; repositories only `flush()`                           |
| Access control | `require_permissions("resource:action")` on the server; never role ids or names |
| Constants      | Statuses in the module's `constants.py`, permissions in `core/permissions.py`   |

## Authentication and authorization

Users, roles and permissions live in this application's database. See [docs/auth.md](docs/auth.md)
for the token flow and security properties.

- Short-lived **access token** (JWT, 15 min) sent as a Bearer header.
- **Refresh token**: opaque, stored hashed, rotated on every use, delivered in an `HttpOnly`
  cookie scoped to `/api/v1/auth`. Replaying a rotated token revokes the whole chain.
- Argon2id password hashing, password policy, per-account lockout and a per-IP login rate limit.
- **Permissions** are `resource:action` codes defined in `app/core/permissions.py`; **roles** are
  data that admins compose from them. System roles: `admin` (everything), `member` (no permissions until an admin grants some).

Add a permission: define it in `core/permissions.py`, run `python -m app.cli seed`, then guard a
route with `Depends(require_permissions(MY_PERMISSION))`.

## Configuration

All settings are environment variables (see `.env.example`). Highlights:

| Variable                                            | Purpose                                                                 |
| --------------------------------------------------- | ----------------------------------------------------------------------- |
| `DATABASE_ENGINE`, `DATABASE_URL`                   | `postgresql`, `mysql`, `singlestore` or `sqlite`; async driver required |
| `SECRET_KEY`                                        | JWT signing key; production requires 32+ random characters              |
| `BACKEND_CORS_ORIGINS`                              | Web app origin(s) allowed to call the API with credentials              |
| `COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN` | Refresh cookie attributes                                               |
| `LOGIN_RATE_LIMIT`, `RATE_LIMIT_STORAGE_URI`        | Login throttling; use a shared store with several workers               |

With `APP_ENV=production` the app refuses to start on an unsafe configuration (weak secret,
debug on, insecure cookie, SQLite) and disables `/docs`.

## Database and migrations

```bash
alembic revision -m "add widgets"    # then write the migration by hand
alembic upgrade head
alembic downgrade -1
```

Write migrations with `op.create_table` and the helpers in `app/db/migration_helpers.py`, and
always implement `downgrade()`. Details and the SingleStore notes: [docs/database.md](docs/database.md).

## Quality checks

```bash
pytest                    # 60+ tests: auth flow, lockout, token reuse, RBAC, CRUD, soft delete
ruff check . && ruff format --check .
pyright
```

## Deploy

PM2 + Nginx scripts are in [`deploy/`](deploy/README.md); a `Dockerfile` is included and the
repository root has a `docker-compose.yml` that runs the API, the web app and PostgreSQL.

## License

[MIT](LICENSE)
