import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.permissions import ADMIN_ROLE, MEMBER_ROLE
from app.db.session import get_session_factory
from app.modules.auth.model import RefreshToken

from .helpers import CSRF, PASSWORD, auth_headers, create_user, login

COOKIE = settings.refresh_cookie_name
COOKIE_PATH = settings.refresh_cookie_path


def _cookie(client: httpx.AsyncClient) -> str:
    value = client.cookies.get(COOKIE)
    assert value, "expected a refresh cookie"
    return value


async def test_login_returns_token_user_and_httponly_cookie(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)

    response = await login(client, "Admin@Example.com")  # email is case-insensitive

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_expire_minutes * 60
    assert body["user"]["email"] == "admin@example.com"
    assert "users:read" in body["user"]["permissions"]
    assert "password_hash" not in response.text

    cookie = response.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert f"path={COOKIE_PATH}" in cookie
    assert COOKIE not in body["access_token"]


async def test_wrong_password_and_unknown_email_look_identical(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)

    wrong = await login(client, "admin@example.com", "Wrong-password-1")
    unknown = await login(client, "ghost@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_inactive_user_cannot_log_in(client: httpx.AsyncClient) -> None:
    await create_user("off@example.com", MEMBER_ROLE, is_active=False)

    assert (await login(client, "off@example.com")).status_code == 401


async def test_account_locks_after_repeated_failures(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)

    for _ in range(settings.login_max_attempts):
        assert (await login(client, "admin@example.com", "Wrong-password-1")).status_code == 401

    # Even the right password is refused while locked, and the response does not say why.
    locked = await login(client, "admin@example.com")
    assert locked.status_code == 401
    assert locked.json()["error"]["message"] == "Invalid email or password."


async def test_successful_login_resets_the_failure_counter(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)

    for _ in range(settings.login_max_attempts - 1):
        await login(client, "admin@example.com", "Wrong-password-1")
    assert (await login(client, "admin@example.com")).status_code == 200
    for _ in range(settings.login_max_attempts - 1):
        await login(client, "admin@example.com", "Wrong-password-1")

    assert (await login(client, "admin@example.com")).status_code == 200


async def test_me_requires_a_valid_bearer_token(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)

    missing = await client.get("/api/v1/auth/me")
    garbage = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    valid = await client.get(
        "/api/v1/auth/me", headers=await auth_headers(client, "admin@example.com")
    )

    assert missing.status_code == garbage.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert valid.status_code == 200
    assert valid.json()["email"] == "admin@example.com"


async def test_refresh_rotates_the_cookie_and_returns_a_new_session(
    client: httpx.AsyncClient,
) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    await login(client, "admin@example.com")
    first_cookie = _cookie(client)

    response = await client.post("/api/v1/auth/refresh", headers=CSRF)

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "admin@example.com"
    assert client.cookies.get(COOKIE) != first_cookie


async def test_refresh_requires_csrf_header_and_cookie(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    await login(client, "admin@example.com")

    without_header = await client.post("/api/v1/auth/refresh")
    assert without_header.status_code == 403

    client.cookies.clear()
    without_cookie = await client.post("/api/v1/auth/refresh", headers=CSRF)
    assert without_cookie.status_code == 401


async def test_replaying_a_rotated_refresh_token_revokes_the_whole_family(
    client: httpx.AsyncClient,
) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    await login(client, "admin@example.com")
    stolen = _cookie(client)
    await client.post("/api/v1/auth/refresh", headers=CSRF)
    legitimate = _cookie(client)

    # The attacker replays the old token (a lone cookie: clear the jar so none is duplicated)...
    client.cookies.clear()
    client.cookies.set(COOKIE, stolen, domain="testserver", path=COOKIE_PATH)
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401

    # ...which also kills the legitimate user's newest token.
    client.cookies.clear()
    client.cookies.set(COOKIE, legitimate, domain="testserver", path=COOKIE_PATH)
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    await login(client, "admin@example.com")
    token = _cookie(client)

    assert (await client.post("/api/v1/auth/logout", headers=CSRF)).status_code == 204

    client.cookies.set(COOKIE, token, domain="testserver", path=COOKIE_PATH)
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401


async def test_refresh_tokens_are_stored_hashed(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    await login(client, "admin@example.com")
    raw = _cookie(client)

    async with get_session_factory()() as db:
        stored = (await db.scalars(select(RefreshToken.token_hash))).all()

    assert len(stored) == 1
    assert raw not in stored
    assert len(stored[0]) == 64


async def test_change_password_signs_out_everywhere(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    headers = await auth_headers(client, "admin@example.com")
    old_cookie = _cookie(client)

    wrong = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "Wrong-password-1", "new_password": "Brand-new-pass-1"},
    )
    assert wrong.status_code == 400

    changed = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "Brand-new-pass-1"},
    )
    assert changed.status_code == 204

    client.cookies.set(COOKIE, old_cookie, domain="testserver", path=COOKIE_PATH)
    assert (await client.post("/api/v1/auth/refresh", headers=CSRF)).status_code == 401
    assert (await login(client, "admin@example.com")).status_code == 401
    assert (await login(client, "admin@example.com", "Brand-new-pass-1")).status_code == 200


async def test_change_password_enforces_strength(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    headers = await auth_headers(client, "admin@example.com")

    response = await client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "weak"},
    )

    assert response.status_code == 422


async def test_update_own_profile(client: httpx.AsyncClient) -> None:
    await create_user("admin@example.com", ADMIN_ROLE)
    headers = await auth_headers(client, "admin@example.com")

    response = await client.patch(
        "/api/v1/auth/me", headers=headers, json={"full_name": "  Jane Doe "}
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Jane Doe"
