import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import Request
from slowapi.errors import RateLimitExceeded

from app.core.config import Settings
from app.core.exceptions import rate_limit_handler, unhandled_exception_handler
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
    assert not_found.json()["error"]["message"] == "Sumber daya tidak ditemukan."
    assert invalid.status_code == 422
    body = invalid.json()["error"]
    assert body["code"] == "validation_error"
    assert body["message"] == "Data yang dikirim tidak valid."
    assert {"email", "password"} <= {item["loc"][-1] for item in body["details"]}


async def test_validation_details_are_indonesian(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    bad_email = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": ""}
    )
    short = await client.post(
        "/api/v1/roles", headers=admin_headers, json={"name": "x", "permissions": "nope"}
    )

    by_field = {i["loc"][-1]: i["message"] for i in bad_email.json()["error"]["details"]}
    assert by_field == {"email": "Format email tidak valid.", "password": "Wajib diisi."}
    by_field = {i["loc"][-1]: i["message"] for i in short.json()["error"]["details"]}
    assert by_field["name"] == "Minimal 2 karakter."
    assert by_field["permissions"] == "Nilai tidak valid."


async def test_method_not_allowed_message_is_indonesian(client: httpx.AsyncClient) -> None:
    response = await client.delete("/health")

    assert response.status_code == 405
    assert response.json()["error"]["message"] == "Metode tidak diizinkan."


async def test_rate_limit_and_unexpected_errors_use_indonesian_messages() -> None:
    request = cast(Request, None)  # the handlers do not read the request
    limit = SimpleNamespace(error_message=None, limit="5 per 1 minute")

    limited = await rate_limit_handler(request, RateLimitExceeded(cast(Any, limit)))
    crashed = await unhandled_exception_handler(request, RuntimeError("boom"))

    assert limited.status_code == 429
    assert json.loads(bytes(limited.body))["error"] == {
        "code": "too_many_requests",
        "message": "Terlalu banyak permintaan. Silakan coba lagi nanti.",
        "details": None,
    }
    assert crashed.status_code == 500
    assert json.loads(bytes(crashed.body))["error"]["message"] == "Terjadi kesalahan tak terduga."


async def test_openapi_lists_the_feature_routes(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    for path in ("/api/v1/auth/login", "/api/v1/users", "/api/v1/roles"):
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
