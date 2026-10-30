import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}


async def test_every_response_has_request_id_and_security_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_unsafe_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})

    assert response.headers["X-Request-ID"] != "bad id\nwith newline"


async def test_errors_use_one_envelope(client: httpx.AsyncClient) -> None:
    not_found = await client.get("/api/v1/does-not-exist")
    invalid = await client.post("/api/v1/auth/login", json={"email": "not-an-email"})

    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422
    body = invalid.json()["error"]
    assert body["code"] == "validation_error"
    assert {"email", "password"} <= {item["loc"][-1] for item in body["details"]}


async def test_openapi_lists_the_feature_routes(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    for path in ("/api/v1/auth/login", "/api/v1/users", "/api/v1/roles", "/api/v1/examples"):
        assert path in paths
    assert "/health" not in paths


async def test_docs_are_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "production")
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        assert (await http.get("/docs")).status_code == 404
        assert (await http.get("/openapi.json")).status_code == 404


def test_production_rejects_unsafe_configuration() -> None:
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        Settings(
            app_env="production",
            secret_key="short",
            database_engine="sqlite",
            database_url="sqlite+aiosqlite:///:memory:",
            _env_file=None,  # type: ignore[call-arg]
        )


def test_production_accepts_a_hardened_configuration() -> None:
    settings = Settings(
        app_env="production",
        secret_key="x" * 40,
        cookie_secure=True,
        database_engine="postgresql",
        database_url="postgresql+asyncpg://u:p@db/app",
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.is_production
    assert settings.refresh_cookie_secure


def test_database_url_must_match_engine_and_be_async() -> None:
    with pytest.raises(ValueError, match="does not match DATABASE_ENGINE"):
        Settings(
            database_engine="mysql",
            database_url="postgresql+asyncpg://u:p@localhost/db",
            _env_file=None,  # type: ignore[call-arg]
        )
    with pytest.raises(ValueError, match="async driver"):
        Settings(
            database_engine="postgresql",
            database_url="postgresql://u:p@localhost/db",
            _env_file=None,  # type: ignore[call-arg]
        )
