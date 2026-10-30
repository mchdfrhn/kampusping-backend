# Authentication

## Token flow

```text
POST /auth/login     {email, password}
  -> 200 {access_token, expires_in, user}  + Set-Cookie: refresh_token (HttpOnly, Path=/api/v1/auth)

GET  /anything       Authorization: Bearer <access_token>

POST /auth/refresh   Cookie: refresh_token, header X-Requested-With
  -> 200 {access_token, ...} + a new refresh cookie; the old refresh token is now revoked

POST /auth/logout    Cookie: refresh_token, header X-Requested-With
  -> 204, refresh chain revoked, cookie cleared
```

A web client keeps the access token **in memory only**. After a page reload it calls
`/auth/refresh` to get a new one; the browser sends the cookie automatically.

## Security properties

| Threat | Mitigation |
| --- | --- |
| Stored credential leak | Argon2id hashes; refresh tokens stored as SHA-256 hashes |
| Token theft from JS | Refresh token is `HttpOnly`; access token lives in memory, 15 min |
| Stolen refresh token | Rotation on every use; replaying a rotated token revokes the whole family |
| CSRF on cookie endpoints | `SameSite` cookie scoped to `/auth` plus a required `X-Requested-With` header (forces a CORS preflight) |
| Brute force | Per-IP login rate limit and per-account lockout (5 failures, 15 minutes) |
| Account enumeration | One error message for unknown email, wrong password, inactive and locked accounts; an Argon2 verification runs even for unknown emails |
| Stale permissions | Permissions are read from the database on every request, never from the token |
| Forged tokens | Algorithm pinned, `exp`/`iat`/`sub`/`type` required, `alg: none` rejected |
| Weak deployment | Production refuses weak `SECRET_KEY`, insecure cookies, debug and SQLite |

Changing a password, deactivating or deleting a user revokes all of that user's refresh tokens.
Existing access tokens stop working immediately for deactivated or deleted users (the user is
loaded on each request) and otherwise expire within the access-token lifetime.

## Permissions model

```text
User --< user_roles >-- Role --< role_permissions >-- Permission ("resource:action")
```

- `Permission` rows are reference data synced from `app/core/permissions.py` by
  `python -m app.cli seed`. Run it on every deploy; it is idempotent.
- `Role` rows are created by administrators. `admin` and `member` are system roles; they cannot be
  renamed or deleted, and `admin` always holds every permission.
- A user's effective permissions are the union of their roles' permissions.
- Authorization is enforced on the server with `require_permissions(...)`. The `permissions`
  list returned by `/auth/me` exists only so a UI can hide controls.

## Operating notes

- Run behind a reverse proxy with `--proxy-headers` so the rate limiter sees real client IPs.
- The default rate-limit store is in-memory and per process; set `RATE_LIMIT_STORAGE_URI`
  (for example `redis://...`) when running several workers.
- `python -m app.cli purge-tokens` removes expired refresh tokens; schedule it daily.
- Two browser tabs refreshing at the same instant can look like token reuse and sign the user
  out. The reference frontend serializes refreshes in a single in-flight request to avoid this.
