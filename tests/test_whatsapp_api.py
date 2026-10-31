import httpx
import pytest
from fastapi import FastAPI

from app.db.session import get_session_factory
from app.modules.settings.model import AppSettings
from app.modules.settings.service import SettingsService
from app.modules.whatsapp.deps import get_provider
from app.modules.whatsapp.fake import FakeProvider
from app.modules.whatsapp.provider import ProviderStatus, ProviderUnavailable

from .messages_helpers import (
    LOGOUT_URL,
    STATUS_URL,
    audit_events,
)

# --- WhatsApp status and logout ------------------------------------------------------------


class QrProvider(FakeProvider):
    async def status(self) -> ProviderStatus:
        return ProviderStatus(state="qr", qr="2@qr-payload")


class UnreachableProvider(FakeProvider):
    async def status(self) -> ProviderStatus:
        raise ProviderUnavailable("Gateway WhatsApp tidak tersedia.")


class BrokenProvider(FakeProvider):
    async def status(self) -> ProviderStatus:
        raise RuntimeError("boom")


async def test_whatsapp_status_uses_provider_and_requires_permission(
    app: FastAPI,
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    pimpinan_headers: dict[str, str],
) -> None:
    assert (await client.get(STATUS_URL, headers=pimpinan_headers)).status_code == 403
    assert (await client.get(STATUS_URL)).status_code == 401

    app.dependency_overrides[get_provider] = lambda: FakeProvider(state="connected")
    connected = await client.get(STATUS_URL, headers=staf_headers)
    assert connected.status_code == 200
    assert connected.json() == {"state": "connected", "qr": None, "paused_reason": None}

    app.dependency_overrides[get_provider] = lambda: QrProvider()
    scanning = await client.get(STATUS_URL, headers=staf_headers)
    assert scanning.json() == {"state": "qr", "qr": "2@qr-payload", "paused_reason": None}

    async with get_session_factory()() as db:
        row = await SettingsService(db).get()
        row.paused_reason = "circuit_breaker"
        await db.commit()
    paused = await client.get(STATUS_URL, headers=staf_headers)
    assert paused.json()["paused_reason"] == "circuit_breaker"


@pytest.mark.parametrize("provider", [UnreachableProvider(), BrokenProvider()])
async def test_whatsapp_status_provider_failure_is_disconnected_not_500(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str], provider: FakeProvider
) -> None:
    app.dependency_overrides[get_provider] = lambda: provider

    response = await client.get(STATUS_URL, headers=staf_headers)

    assert response.status_code == 200
    assert response.json() == {"state": "disconnected", "qr": None, "paused_reason": None}


async def test_whatsapp_status_creates_the_default_settings_row(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    app.dependency_overrides[get_provider] = lambda: FakeProvider()

    await client.get(STATUS_URL, headers=staf_headers)

    async with get_session_factory()() as db:
        assert await db.get(AppSettings, 1) is not None


async def test_logout_calls_provider_and_audits(
    app: FastAPI, client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    provider = FakeProvider(state="connected")
    app.dependency_overrides[get_provider] = lambda: provider

    response = await client.post(LOGOUT_URL, headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}
    assert provider.state == "disconnected"
    events = await audit_events("whatsapp.logout")
    assert len(events) == 1
    assert events[0].actor == "admin@example.com"
    assert events[0].entity == "whatsapp"


async def test_logout_provider_error_is_502_without_audit(
    app: FastAPI, client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    class FailingLogout(FakeProvider):
        async def logout(self) -> None:
            raise ProviderUnavailable("http://127.0.0.1:8095 is down")

    app.dependency_overrides[get_provider] = lambda: FailingLogout()

    response = await client.post(LOGOUT_URL, headers=admin_headers)

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "application_error"
    assert error["message"] == "Gagal memutuskan sesi WhatsApp."
    assert "127.0.0.1" not in response.text
    assert await audit_events("whatsapp.logout") == []
