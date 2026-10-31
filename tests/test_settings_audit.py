from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.db.session import get_session_factory
from app.modules.audit.model import AuditEvent
from app.modules.audit.service import record_audit
from app.modules.settings.constants import SETTINGS_ROW_ID
from app.modules.settings.model import AppSettings
from app.modules.settings.service import SettingsService
from app.shared.clock import local_now, local_today

SETTINGS_URL = "/api/v1/settings"
AUDIT_URL = "/api/v1/audit-events"


async def test_get_settings_returns_defaults(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get(SETTINGS_URL, headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["window_start"].startswith("08:00")
    assert body["window_end"].startswith("17:00")
    assert body["working_days"] == [1, 2, 3, 4, 5]
    assert body["auto_send_enabled"] is False
    assert body["daily_cap"] == 200
    assert body["throttle_min_s"] == 8
    assert body["throttle_max_s"] == 20
    assert body["max_attempts"] == 3
    assert body["consecutive_failure_limit"] == 5
    assert body["escalation_offset_days"] == 14
    assert body["paused_reason"] is None


def valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "campus_name": "Kampus Contoh",
        "finance_contact": "Bagian Keuangan 0812-0000-0000",
        "payment_link_siakad": "https://siakad.example.ac.id/bayar",
        "payment_guide_sipema": "Buka SIPEMA lalu pilih Pembayaran.",
        "window_start": "09:00:00",
        "window_end": "16:00:00",
        "working_days": [1, 2, 3, 4, 5, 6],
        "auto_send_enabled": True,
        "daily_cap": 150,
        "throttle_min_s": 5,
        "throttle_max_s": 15,
        "max_attempts": 4,
        "consecutive_failure_limit": 6,
        "escalation_offset_days": 10,
    }
    payload.update(overrides)
    return payload


async def test_update_settings_roundtrip(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.put(SETTINGS_URL, json=valid_payload(), headers=admin_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["campus_name"] == "Kampus Contoh"
    assert body["working_days"] == [1, 2, 3, 4, 5, 6]
    assert body["auto_send_enabled"] is True

    again = await client.get(SETTINGS_URL, headers=admin_headers)
    assert again.json()["daily_cap"] == 150
    assert again.json()["window_start"].startswith("09:00")


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"window_start": "17:00:00", "window_end": "08:00:00"}, "window_end"),
        ({"window_start": "09:00:00", "window_end": "09:00:00"}, "window_end"),
        ({"throttle_min_s": 30, "throttle_max_s": 10}, "throttle_max_s"),
        ({"working_days": [0, 1, 2]}, "working_days"),
        ({"working_days": [1, 8]}, "working_days"),
        ({"working_days": []}, "working_days"),
        ({"working_days": [1, 1, 2]}, "working_days"),
        ({"daily_cap": 0}, "daily_cap"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"max_attempts": 11}, "max_attempts"),
        ({"consecutive_failure_limit": 0}, "consecutive_failure_limit"),
        ({"escalation_offset_days": 0}, "escalation_offset_days"),
    ],
)
async def test_update_settings_validates(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    override: dict[str, Any],
    field: str,
) -> None:
    response = await client.put(SETTINGS_URL, json=valid_payload(**override), headers=admin_headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(field in detail["loc"] for detail in error["details"])
    assert all(detail["message"] != "Value error" for detail in error["details"])


async def test_validation_messages_are_indonesian(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.put(
        SETTINGS_URL,
        json=valid_payload(window_start="17:00:00", window_end="08:00:00"),
        headers=admin_headers,
    )

    messages = [detail["message"] for detail in response.json()["error"]["details"]]
    assert "Jam mulai harus lebih awal dari jam selesai." in messages


@pytest.mark.parametrize("field", ["window_start", "window_end"])
async def test_window_times_with_timezone_are_rejected_not_a_500(
    client: httpx.AsyncClient, admin_headers: dict[str, str], field: str
) -> None:
    response = await client.put(
        SETTINGS_URL, json=valid_payload(**{field: "17:00+07:00"}), headers=admin_headers
    )

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert any(
        field in detail["loc"] and detail["message"] == "Jam harus tanpa zona waktu."
        for detail in details
    )


async def test_settings_require_permissions(
    client: httpx.AsyncClient,
    pimpinan_headers: dict[str, str],
    staf_headers: dict[str, str],
) -> None:
    assert (await client.get(SETTINGS_URL, headers=pimpinan_headers)).status_code == 403
    assert (
        await client.put(SETTINGS_URL, json=valid_payload(), headers=pimpinan_headers)
    ).status_code == 403
    assert (
        await client.post(f"{SETTINGS_URL}/resume", headers=pimpinan_headers)
    ).status_code == 403

    assert (await client.get(SETTINGS_URL, headers=staf_headers)).status_code == 200
    assert (
        await client.put(SETTINGS_URL, json=valid_payload(), headers=staf_headers)
    ).status_code == 200

    assert (await client.get(SETTINGS_URL)).status_code == 401


async def test_update_and_resume_write_audit_events(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await client.put(
        SETTINGS_URL, json=valid_payload(daily_cap=111, campus_name="Baru"), headers=admin_headers
    )
    await client.post(f"{SETTINGS_URL}/resume", headers=admin_headers)

    response = await client.get(AUDIT_URL, headers=admin_headers)

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    # Default order: newest first.
    assert [item["action"] for item in page["items"]] == ["settings.resumed", "settings.updated"]
    updated = page["items"][1]
    assert updated["actor"] == "admin@example.com"
    assert updated["entity"] == "settings"
    assert "daily_cap" in updated["detail"]["changed"]
    assert "campus_name" in updated["detail"]["changed"]
    assert "window_start" in updated["detail"]["changed"]

    filtered = await client.get(
        AUDIT_URL, params={"action": "settings.resumed"}, headers=admin_headers
    )
    assert filtered.json()["total"] == 1
    by_entity = await client.get(AUDIT_URL, params={"entity": "nothing"}, headers=admin_headers)
    assert by_entity.json()["total"] == 0

    ascending = await client.get(
        AUDIT_URL, params={"sort_by": "at", "sort_order": "asc"}, headers=admin_headers
    )
    assert [item["action"] for item in ascending.json()["items"]] == [
        "settings.updated",
        "settings.resumed",
    ]


async def test_unchanged_update_lists_no_changed_fields(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await client.put(SETTINGS_URL, json=valid_payload(), headers=admin_headers)
    await client.put(SETTINGS_URL, json=valid_payload(), headers=admin_headers)

    items = (await client.get(AUDIT_URL, headers=admin_headers)).json()["items"]
    assert items[0]["detail"]["changed"] == []


async def test_resume_clears_paused_reason(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await client.get(SETTINGS_URL, headers=admin_headers)
    async with get_session_factory()() as db:
        row = await db.scalar(select(AppSettings))
        assert row is not None
        row.paused_reason = "Gateway gagal berulang kali"
        row.consecutive_failures = 5
        await db.commit()

    paused = await client.get(SETTINGS_URL, headers=admin_headers)
    assert paused.json()["paused_reason"] == "Gateway gagal berulang kali"

    response = await client.post(f"{SETTINGS_URL}/resume", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["paused_reason"] is None
    async with get_session_factory()() as db:
        row = await db.scalar(select(AppSettings))
        assert row is not None
        assert row.paused_reason is None
        assert row.consecutive_failures == 0


async def test_put_cannot_change_internal_counters(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.put(
        SETTINGS_URL,
        json=valid_payload(consecutive_failures=3, paused_reason="x"),
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["consecutive_failures"] == 0
    assert response.json()["paused_reason"] is None


async def test_audit_events_admin_only(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    pimpinan_headers: dict[str, str],
) -> None:
    assert (await client.get(AUDIT_URL, headers=staf_headers)).status_code == 403
    assert (await client.get(AUDIT_URL, headers=pimpinan_headers)).status_code == 403
    assert (await client.get(AUDIT_URL)).status_code == 401


async def test_record_audit_flushes_without_committing() -> None:
    async with get_session_factory()() as db:
        await record_audit(
            db, actor="sistem", action="worker.tick", entity="job", entity_id=7, detail={"n": 1}
        )
        await db.rollback()
    async with get_session_factory()() as db:
        assert await db.scalar(select(AuditEvent)) is None

    async with get_session_factory()() as db:
        await record_audit(db, actor="sistem", action="worker.tick", entity="job", entity_id=7)
        await db.commit()
    async with get_session_factory()() as db:
        event = await db.scalar(select(AuditEvent))
        assert event is not None
        assert event.entity_id == "7"
        assert event.at is not None


async def test_get_creates_the_default_row_once() -> None:
    async with get_session_factory()() as db:
        first = await SettingsService(db).get()
        second = await SettingsService(db).get()
        assert first is second
        await db.commit()
    async with get_session_factory()() as db:
        assert len((await db.scalars(select(AppSettings))).all()) == 1


async def test_get_survives_losing_the_first_access_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another session inserts the row between our existence check and our own insert."""
    async with get_session_factory()() as loser:
        real_get = loser.get
        raced = False

        async def get_then_lose_the_race(*args: Any, **kwargs: Any) -> Any:
            nonlocal raced
            found = await real_get(*args, **kwargs)
            if found is None and not raced:
                raced = True
                async with get_session_factory()() as winner:
                    winner.add(AppSettings(id=SETTINGS_ROW_ID, campus_name="Pemenang"))
                    await winner.commit()
            return found

        monkeypatch.setattr(loser, "get", get_then_lose_the_race)

        row = await SettingsService(loser).get()  # must not raise IntegrityError

        assert raced
        assert row.id == SETTINGS_ROW_ID
        assert row.campus_name == "Pemenang"
        await loser.commit()
    async with get_session_factory()() as db:
        assert len((await db.scalars(select(AppSettings))).all()) == 1


def test_local_today_crosses_midnight_in_jakarta() -> None:
    now = datetime(2026, 10, 31, 17, 30, tzinfo=UTC)

    assert local_today(now, "Asia/Jakarta").isoformat() == "2026-11-01"
    assert local_now(now, "Asia/Jakarta").hour == 0
    assert local_now(now, "Asia/Jakarta").utcoffset() is not None
    assert local_today(datetime(2026, 10, 31, 16, 59, tzinfo=UTC), "Asia/Jakarta").isoformat() == (
        "2026-10-31"
    )


def test_clock_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        local_today(datetime(2026, 10, 31, 12, 0), "Asia/Jakarta")
    with pytest.raises(ValueError, match="timezone-aware"):
        local_now(datetime(2026, 10, 31, 12, 0), "Asia/Jakarta")


def test_app_timezone_is_validated() -> None:
    assert Settings(app_timezone="Asia/Jakarta").app_timezone == "Asia/Jakarta"
    with pytest.raises(ValidationError):
        Settings(app_timezone="Mars/Olympus")


def test_new_worker_and_gateway_settings_have_safe_defaults() -> None:
    config = Settings()
    assert config.app_timezone == "Asia/Jakarta"
    assert config.worker_tick_seconds == 60
    assert config.wa_provider == "fake"
    assert config.wa_gateway_api_key is None
    with pytest.raises(ValidationError):
        Settings(worker_tick_seconds=0)


def test_production_gateway_requires_both_secrets() -> None:
    base: dict[str, Any] = {
        "app_env": "production",
        "app_debug": False,
        "secret_key": "x" * 40,
        "database_engine": "postgresql",
        "database_url": "postgresql+asyncpg://u:p@db:5432/x",
        "cookie_secure": True,
        "wa_provider": "gateway",
    }
    with pytest.raises(ValidationError, match="WA_GATEWAY_API_KEY"):
        Settings(**base)
    with pytest.raises(ValidationError, match="WA_WEBHOOK_SECRET"):
        Settings(**base, wa_gateway_api_key=SecretStr("key"))
    with pytest.raises(ValidationError, match="WA_WEBHOOK_SECRET"):
        Settings(**base, wa_gateway_api_key=SecretStr("key"), wa_webhook_secret=SecretStr(""))
    ok = Settings(**base, wa_gateway_api_key=SecretStr("key"), wa_webhook_secret=SecretStr("hook"))
    assert ok.wa_provider == "gateway"
    # The fake provider needs no secrets, even in production.
    assert Settings(**{**base, "wa_provider": "fake"}).wa_provider == "fake"
