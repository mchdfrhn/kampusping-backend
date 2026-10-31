import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.deps import get_clock
from app.core.config import settings
from app.db.session import get_session_factory
from app.modules.messages.model import Message
from app.modules.whatsapp import router as whatsapp_router
from app.modules.whatsapp.constants import MAX_WEBHOOK_BODY_BYTES
from app.modules.whatsapp.signature import sign_body

from .messages_helpers import (
    NOW,
    SECRET,
    WEBHOOK_URL,
    Clock,
    add_message,
    get_message,
    post_event,
    send_raw,
    utc,
)


@pytest.fixture
def clock(app: FastAPI) -> Clock:
    fake = Clock()
    app.dependency_overrides[get_clock] = lambda: fake
    return fake


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "wa_webhook_secret", SecretStr(SECRET))
    return SECRET


# --- Webhook: authentication ---------------------------------------------------------------

STATUS_EVENT = {"type": "status", "provider_message_id": "wamid-1", "state": "delivered"}


async def sent_message() -> int:
    return await add_message(status="sent", provider_message_id="wamid-1", sent_at=NOW)


async def test_webhook_rejects_missing_or_bad_signature_with_401(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    message_id = await sent_message()
    body = json.dumps(STATUS_EVENT).encode()
    good = sign_body(SECRET, body)
    digest = good.removeprefix("sha256=")
    cases = {
        "missing": None,
        "empty": "",
        "wrong secret": sign_body("another-secret", body),
        "tampered body": sign_body(SECRET, body + b" "),
        "wrong prefix": "sha1=" + digest,
        "no prefix": digest,
        "uppercase digest": "sha256=" + digest.upper(),
    }

    for name, signature in cases.items():
        response = await send_raw(client, body, signature=signature)
        assert response.status_code == 401, name
        error = response.json()["error"]
        assert error["code"] == "unauthorized", name
        assert error["message"] == "Tanda tangan tidak valid.", name
        assert error["details"] is None, name

    assert (await get_message(message_id)).status == "sent"
    ok = await send_raw(client, body, signature=good)
    assert ok.status_code == 202


async def test_webhook_rejects_everything_when_secret_not_configured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    message_id = await sent_message()
    body = json.dumps(STATUS_EVENT).encode()

    for configured in (None, SecretStr("")):
        monkeypatch.setattr(settings, "wa_webhook_secret", configured)
        for signature in (None, sign_body("", body), sign_body(SECRET, body)):
            response = await send_raw(client, body, signature=signature)
            assert response.status_code == 401
            assert response.json()["error"]["message"] == "Tanda tangan tidak valid."

    assert (await get_message(message_id)).status == "sent"


async def test_webhook_warns_once_per_process_when_secret_not_configured(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "wa_webhook_secret", None)
    monkeypatch.setattr(whatsapp_router, "_warned_missing_secret", False)

    with caplog.at_level(logging.WARNING, logger=whatsapp_router.logger.name):
        for _ in range(3):
            await send_raw(client, b"{}", signature=None)

    warnings = [r for r in caplog.records if "WA_WEBHOOK_SECRET" in r.getMessage()]
    assert len(warnings) == 1


async def test_webhook_needs_no_user_auth_or_csrf(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    response = await post_event(client, {"type": "connection", "state": "connected"})

    assert response.status_code == 202  # no Authorization header, no CSRF header


# --- Webhook: events -----------------------------------------------------------------------


async def test_webhook_status_updates_delivered_then_read_and_never_regresses(
    client: httpx.AsyncClient, clock: Clock, webhook_secret: str
) -> None:
    message_id = await sent_message()
    delivered_at = NOW + timedelta(minutes=1)
    read_at = NOW + timedelta(minutes=5)

    clock.now = delivered_at
    response = await post_event(client, STATUS_EVENT)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    stored = await get_message(message_id)
    assert stored.status == "delivered"
    assert utc(stored.delivered_at) == delivered_at
    assert stored.read_at is None
    assert stored.updated_by == "whatsapp-webhook"

    clock.now = delivered_at + timedelta(minutes=1)  # a duplicate keeps the first timestamp
    await post_event(client, STATUS_EVENT)
    assert utc((await get_message(message_id)).delivered_at) == delivered_at

    clock.now = read_at
    await post_event(client, {**STATUS_EVENT, "state": "read"})
    stored = await get_message(message_id)
    assert stored.status == "read"
    assert utc(stored.read_at) == read_at
    assert utc(stored.delivered_at) == delivered_at

    clock.now = read_at + timedelta(minutes=9)  # a late "delivered" and a duplicate "read"
    assert (await post_event(client, STATUS_EVENT)).status_code == 202
    assert (await post_event(client, {**STATUS_EVENT, "state": "read"})).status_code == 202
    stored = await get_message(message_id)
    assert stored.status == "read"
    assert utc(stored.read_at) == read_at
    assert utc(stored.delivered_at) == delivered_at


async def test_webhook_read_straight_from_sent_also_sets_delivered_at(
    client: httpx.AsyncClient, clock: Clock, webhook_secret: str
) -> None:
    message_id = await sent_message()
    clock.now = NOW + timedelta(minutes=3)

    await post_event(client, {**STATUS_EVENT, "state": "read"})

    stored = await get_message(message_id)
    assert stored.status == "read"
    assert utc(stored.read_at) == clock.now
    assert utc(stored.delivered_at) == clock.now


async def test_webhook_failed_only_from_sent(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    sent_id = await add_message(status="sent", provider_message_id="w-sent", sent_at=NOW)
    delivered_id = await add_message(status="delivered", provider_message_id="w-del")
    read_id = await add_message(status="read", provider_message_id="w-read")
    failed_id = await add_message(status="failed", provider_message_id="w-fail", error="lama")
    cancelled_id = await add_message(status="cancelled", provider_message_id="w-can")

    for provider_id in ("w-sent", "w-del", "w-read", "w-fail", "w-can"):
        event = {"type": "status", "provider_message_id": provider_id, "state": "failed"}
        assert (await post_event(client, event)).status_code == 202

    sent = await get_message(sent_id)
    assert sent.status == "failed"
    assert sent.error == "Gagal terkirim ke WhatsApp."
    assert (await get_message(delivered_id)).status == "delivered"
    assert (await get_message(read_id)).status == "read"
    failed = await get_message(failed_id)
    assert (failed.status, failed.error) == ("failed", "lama")  # duplicate left it untouched
    assert (await get_message(cancelled_id)).status == "cancelled"


async def test_webhook_unknown_id_is_202(client: httpx.AsyncClient, webhook_secret: str) -> None:
    message_id = await sent_message()

    for state in ("delivered", "read", "failed"):
        event = {"type": "status", "provider_message_id": "wamid-unknown", "state": state}
        response = await post_event(client, event)
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}

    assert (await get_message(message_id)).status == "sent"


async def test_webhook_ignores_soft_deleted_messages(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    message_id = await sent_message()
    async with get_session_factory()() as db:
        row = await db.get(Message, message_id)
        assert row is not None
        row.deleted_at = NOW
        await db.commit()

    assert (await post_event(client, STATUS_EVENT)).status_code == 202
    assert (await get_message(message_id)).status == "sent"


@pytest.mark.parametrize(
    "event",
    [
        {"type": "connection", "state": "connected"},
        {"type": "connection", "state": "disconnected", "reason": "logged out"},
        {"type": "inbound", "from": "6281234567890", "text": "STOP", "id": "x"},
        {"type": "something-new", "payload": {"a": 1}},
        {"no_type": True},
        {"type": 5},
    ],
)
async def test_webhook_connection_and_inbound_are_acknowledged_and_ignored(
    client: httpx.AsyncClient, webhook_secret: str, event: dict[str, Any]
) -> None:
    message_id = await sent_message()

    response = await post_event(client, event)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    stored = await get_message(message_id)
    assert (stored.status, stored.delivered_at, stored.read_at) == ("sent", None, None)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"",
        b"\xff\xfe",
        b"[1, 2, 3]",
        b'"status"',
        b"null",
        pytest.param(b"[" * 20_000, id="deeply-nested"),
    ],
)
async def test_webhook_invalid_json_is_422_after_valid_signature(
    client: httpx.AsyncClient, webhook_secret: str, body: bytes
) -> None:
    response = await send_raw(client, body, signature=sign_body(SECRET, body))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "event",
    [
        {"type": "status"},
        {"type": "status", "provider_message_id": "wamid-1"},
        {"type": "status", "provider_message_id": "wamid-1", "state": "exploded"},
        {"type": "status", "provider_message_id": "", "state": "read"},
        {"type": "status", "provider_message_id": 7, "state": "read"},
    ],
)
async def test_webhook_malformed_status_event_is_422(
    client: httpx.AsyncClient, webhook_secret: str, event: dict[str, Any]
) -> None:
    message_id = await sent_message()

    response = await post_event(client, event)

    assert response.status_code == 422
    assert (await get_message(message_id)).status == "sent"


async def test_webhook_invalid_json_without_signature_is_401_not_422(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    response = await send_raw(client, b"not json", signature=None)

    assert response.status_code == 401


async def test_webhook_body_too_large_is_413(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    limit = 64 * 1024
    skeleton = json.dumps({"type": "connection", "pad": ""}).encode()

    def padded(size: int) -> bytes:
        return json.dumps({"type": "connection", "pad": "a" * (size - len(skeleton))}).encode()

    exact = padded(limit)
    assert len(exact) == limit
    assert (await send_raw(client, exact, signature=sign_body(SECRET, exact))).status_code == 202

    too_big = padded(limit + 1)
    assert len(too_big) == limit + 1
    response = await send_raw(client, too_big, signature=sign_body(SECRET, too_big))
    assert response.status_code == 413
    assert response.json()["error"]["message"] == "Isi permintaan terlalu besar."

    unsigned = await send_raw(client, too_big, signature=None)  # size is checked before the HMAC
    assert unsigned.status_code == 413


async def test_webhook_receipts_never_touch_failed_cancelled_or_queued_rows(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    """Only a ``sent`` (or, for ``read``, ``delivered``) row may take a delivered/read receipt."""
    ids = {
        "failed": await add_message(status="failed", provider_message_id="w-f", error="lama"),
        "cancelled": await add_message(status="cancelled", provider_message_id="w-c"),
        "queued": await add_message(status="queued", provider_message_id="w-q"),
    }
    provider_ids = {"failed": "w-f", "cancelled": "w-c", "queued": "w-q"}

    for name, provider_id in provider_ids.items():
        for state in ("delivered", "read"):
            event = {"type": "status", "provider_message_id": provider_id, "state": state}
            assert (await post_event(client, event)).status_code == 202, (name, state)

    for name, message_id in ids.items():
        stored = await get_message(message_id)
        assert stored.status == name
        assert stored.delivered_at is None
        assert stored.read_at is None
    assert (await get_message(ids["failed"])).error == "lama"


async def test_webhook_receipt_for_a_sending_row_is_a_dropped_no_op(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    # Documented dropped-receipt behaviour: a ``sending`` row has no provider_message_id yet (the
    # sender stores it together with the ``sent`` status), so a receipt that races ahead of that
    # write is an unknown id and is acknowledged as a no-op. The gateway must delay and retry
    # receipts for ids the backend does not know yet (see the ADR 0002 notes).
    message_id = await add_message(status="sending")
    assert (await get_message(message_id)).provider_message_id is None

    for state in ("delivered", "read", "failed"):
        event = {"type": "status", "provider_message_id": "wamid-racing", "state": state}
        response = await post_event(client, event)
        assert response.status_code == 202
        assert response.json() == {"status": "accepted"}

    stored = await get_message(message_id)
    assert (stored.status, stored.provider_message_id) == ("sending", None)
    assert (stored.delivered_at, stored.read_at, stored.error) == (None, None, None)


# --- Webhook: streamed bodies (no Content-Length) ------------------------------------------


async def chunked(body: bytes, size: int = 4096) -> AsyncIterator[bytes]:
    for start in range(0, len(body), size):
        yield body[start : start + size]


async def post_chunked(client: httpx.AsyncClient, body: bytes, *, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature
    request = client.build_request("POST", WEBHOOK_URL, content=chunked(body), headers=headers)
    assert "content-length" not in request.headers  # the limit must be enforced while streaming
    assert request.headers["transfer-encoding"] == "chunked"
    return await client.send(request)


def padded_connection_event(size: int) -> bytes:
    skeleton = json.dumps({"type": "connection", "pad": ""}).encode()
    return json.dumps({"type": "connection", "pad": "a" * (size - len(skeleton))}).encode()


async def test_webhook_chunked_body_at_the_limit_is_accepted(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    body = padded_connection_event(MAX_WEBHOOK_BODY_BYTES)
    assert len(body) == MAX_WEBHOOK_BODY_BYTES

    response = await post_chunked(client, body, signature=sign_body(SECRET, body))

    assert response.status_code == 202


async def test_webhook_chunked_status_event_is_applied(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    message_id = await sent_message()
    body = json.dumps(STATUS_EVENT).encode()

    response = await post_chunked(client, body, signature=sign_body(SECRET, body))

    assert response.status_code == 202
    assert (await get_message(message_id)).status == "delivered"


async def test_webhook_chunked_body_over_the_limit_is_413(
    client: httpx.AsyncClient, webhook_secret: str
) -> None:
    body = padded_connection_event(MAX_WEBHOOK_BODY_BYTES + 1)
    assert len(body) == MAX_WEBHOOK_BODY_BYTES + 1

    signed = await post_chunked(client, body, signature=sign_body(SECRET, body))
    unsigned = await post_chunked(client, body, signature=None)  # size comes before the HMAC

    assert signed.status_code == 413
    assert signed.json()["error"]["message"] == "Isi permintaan terlalu besar."
    assert unsigned.status_code == 413
