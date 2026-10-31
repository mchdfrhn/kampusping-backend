from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import event

from app.db.session import get_engine, get_session_factory
from app.modules.messages.log_repository import MessageLogRepository
from app.modules.messages.model import Message

from .messages_helpers import (
    BODY,
    FULL_PHONE,
    LOGOUT_URL,
    MESSAGES_URL,
    NOW,
    STATUS_URL,
    add_message,
    add_student,
    audit_events,
    get_message,
)

# --- List, filters, sorting ----------------------------------------------------------------


async def test_list_filter_and_pagination(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    budi = await add_student(nim="20210001", name="Budi Santoso")
    sari = await add_student(nim="20210002", name="Sari Dewi")
    first = await add_message(budi, scheduled=date(2026, 11, 1), status="sent")
    second = await add_message(budi, scheduled=date(2026, 11, 2), status="queued")
    third = await add_message(sari, scheduled=date(2026, 11, 3), status="queued")
    fourth = await add_message(sari, scheduled=date(2026, 11, 4), status="failed")

    everything = await client.get(MESSAGES_URL, headers=staf_headers)
    assert everything.status_code == 200
    page = everything.json()
    assert page["total"] == 4
    assert {item["id"] for item in page["items"]} == {first, second, third, fourth}

    queued = (
        await client.get(MESSAGES_URL, params={"status": "queued"}, headers=staf_headers)
    ).json()
    assert {item["id"] for item in queued["items"]} == {second, third}

    dates = (
        await client.get(
            MESSAGES_URL,
            params={"date_from": "2026-11-02", "date_to": "2026-11-03"},
            headers=staf_headers,
        )
    ).json()
    assert {item["id"] for item in dates["items"]} == {second, third}  # both bounds inclusive

    by_student = (
        await client.get(MESSAGES_URL, params={"student_id": sari}, headers=staf_headers)
    ).json()
    assert {item["id"] for item in by_student["items"]} == {third, fourth}
    assert {item["student"]["nim"] for item in by_student["items"]} == {"20210002"}

    ordered = (
        await client.get(
            MESSAGES_URL,
            params={"sort_by": "scheduled_date", "sort_order": "asc", "page_size": 3},
            headers=staf_headers,
        )
    ).json()
    assert [item["id"] for item in ordered["items"]] == [first, second, third]
    assert ordered["total"] == 4
    assert ordered["page_size"] == 3

    second_page = (
        await client.get(
            MESSAGES_URL,
            params={"sort_by": "scheduled_date", "sort_order": "asc", "page": 2, "page_size": 3},
            headers=staf_headers,
        )
    ).json()
    assert [item["id"] for item in second_page["items"]] == [fourth]


async def test_list_filters_by_invoice_and_defaults_to_newest_queued_first(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await add_message()  # queued_at = NOW - 1h * n, so later rows are older
    second = await add_message()
    async with get_session_factory()() as db:
        row = await db.get(Message, first)
        assert row is not None
        invoice_id = row.invoice_id

    body = (await client.get(MESSAGES_URL, headers=staf_headers)).json()
    assert [item["id"] for item in body["items"]] == [first, second]  # queued_at descending

    one = (
        await client.get(MESSAGES_URL, params={"invoice_id": invoice_id}, headers=staf_headers)
    ).json()
    assert [item["id"] for item in one["items"]] == [first]


@pytest.mark.parametrize(
    "params",
    [{"status": "bogus"}, {"sort_by": "body"}, {"sort_by": "phone"}, {"date_from": "not-a-date"}],
)
async def test_list_rejects_unknown_status_sort_and_dates(
    client: httpx.AsyncClient, staf_headers: dict[str, str], params: dict[str, str]
) -> None:
    response = await client.get(MESSAGES_URL, params=params, headers=staf_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_item_shape(client: httpx.AsyncClient, staf_headers: dict[str, str]) -> None:
    message_id = await add_message(status="sent", attempts=1, sent_at=NOW, provider_message_id="w1")

    item = (await client.get(MESSAGES_URL, headers=staf_headers)).json()["items"][0]

    assert set(item) == {
        "id",
        "student",
        "phone_masked",
        "invoice_id",
        "rule_id",
        "kind",
        "scheduled_date",
        "body",
        "status",
        "cancel_reason",
        "attempts",
        "error",
        "queued_at",
        "sent_at",
        "delivered_at",
        "read_at",
        "created_at",
    }
    assert item["id"] == message_id
    assert set(item["student"]) == {"id", "nim", "name"}
    assert item["student"]["name"] == "Budi Santoso"
    assert item["phone_masked"] == "******7890"
    assert item["body"] == BODY
    assert item["scheduled_date"] == "2026-11-02"
    assert item["attempts"] == 1
    assert "provider_message_id" not in item


async def test_list_loads_students_without_one_query_per_row(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    for _ in range(5):
        await add_message()
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "FROM students" in statement:
            statements.append(statement)

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        response = await client.get(MESSAGES_URL, headers=staf_headers)
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 5
    assert len(statements) == 1


async def test_get_message_and_404_for_unknown_or_deleted(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    message_id = await add_message()
    deleted_id = await add_message()
    async with get_session_factory()() as db:
        row = await db.get(Message, deleted_id)
        assert row is not None
        row.deleted_at = NOW
        await db.commit()

    found = await client.get(f"{MESSAGES_URL}/{message_id}", headers=staf_headers)
    assert found.status_code == 200
    assert found.json()["id"] == message_id
    assert found.json()["student"]["nim"].startswith("2021")

    for missing in (999_999, deleted_id):
        response = await client.get(f"{MESSAGES_URL}/{missing}", headers=staf_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    listed = (await client.get(MESSAGES_URL, headers=staf_headers)).json()
    assert [item["id"] for item in listed["items"]] == [message_id]


async def test_phone_is_masked_in_responses(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    message_id = await add_message()
    bodies = [
        (await client.get(MESSAGES_URL, headers=staf_headers)).text,
        (await client.get(f"{MESSAGES_URL}/{message_id}", headers=staf_headers)).text,
        (await client.post(f"{MESSAGES_URL}/{message_id}/cancel", headers=staf_headers)).text,
        (await client.get(f"{MESSAGES_URL}/{message_id}", headers=staf_headers)).text,
    ]

    for text in bodies:
        assert FULL_PHONE not in text
        assert FULL_PHONE.removeprefix("+") not in text
        assert "081234567890" not in text
        assert "******7890" in text


# --- Cancel --------------------------------------------------------------------------------


async def test_cancel_only_queued(client: httpx.AsyncClient, staf_headers: dict[str, str]) -> None:
    queued = await add_message(status="queued")

    response = await client.post(f"{MESSAGES_URL}/{queued}/cancel", headers=staf_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == queued
    assert body["status"] == "cancelled"
    assert body["cancel_reason"] == "manual"
    assert body["body"] == BODY  # the cancelled message keeps its fields
    assert body["scheduled_date"] == "2026-11-02"
    assert body["student"]["name"] == "Budi Santoso"
    stored = await get_message(queued)
    assert (stored.status, stored.cancel_reason, stored.updated_by) == (
        "cancelled",
        "manual",
        "staf@example.com",
    )
    assert stored.body == BODY

    events = await audit_events("message.cancelled")
    assert len(events) == 1
    assert events[0].actor == "staf@example.com"
    assert events[0].entity == "message"
    assert events[0].entity_id == str(queued)
    assert events[0].detail == {"message_id": queued}


@pytest.mark.parametrize("status", ["sending", "sent", "delivered", "read", "failed", "cancelled"])
async def test_cancel_refuses_every_status_but_queued(
    client: httpx.AsyncClient, staf_headers: dict[str, str], status: str
) -> None:
    message_id = await add_message(
        status=status, cancel_reason="paid" if status == "cancelled" else None
    )

    response = await client.post(f"{MESSAGES_URL}/{message_id}/cancel", headers=staf_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert (
        response.json()["error"]["message"] == "Hanya pesan berstatus antre yang dapat dibatalkan."
    )
    stored = await get_message(message_id)
    assert stored.status == status
    assert stored.cancel_reason == ("paid" if status == "cancelled" else None)
    assert await audit_events("message.cancelled") == []


async def test_cancel_unknown_is_404(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await client.post(f"{MESSAGES_URL}/999999/cancel", headers=staf_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert await audit_events("message.cancelled") == []


async def test_cancel_soft_deleted_is_404(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    message_id = await add_message()
    async with get_session_factory()() as db:
        row = await db.get(Message, message_id)
        assert row is not None
        row.deleted_at = NOW
        await db.commit()

    response = await client.post(f"{MESSAGES_URL}/{message_id}/cancel", headers=staf_headers)

    assert response.status_code == 404
    assert (await get_message(message_id)).status == "queued"


async def test_cancel_loses_race_against_claim(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sender claims the message after the request started: the conditional UPDATE must lose."""
    message_id = await add_message(status="queued")
    original = MessageLogRepository.cancel_if_queued

    async def claimed_first(self: MessageLogRepository, mid: int, **kwargs: Any) -> int:
        async with get_session_factory()() as other:
            row = await other.get(Message, mid)
            assert row is not None
            row.status = "sending"
            await other.commit()
        return await original(self, mid, **kwargs)

    monkeypatch.setattr(MessageLogRepository, "cancel_if_queued", claimed_first)

    response = await client.post(f"{MESSAGES_URL}/{message_id}/cancel", headers=staf_headers)

    assert response.status_code == 409
    stored = await get_message(message_id)
    assert stored.status == "sending"
    assert stored.cancel_reason is None
    assert await audit_events("message.cancelled") == []


# --- Permission matrix ---------------------------------------------------------------------

ENDPOINTS = [
    ("GET", MESSAGES_URL),
    ("GET", f"{MESSAGES_URL}/1"),
    ("POST", f"{MESSAGES_URL}/1/cancel"),
    ("GET", STATUS_URL),
    ("POST", LOGOUT_URL),
]


@pytest.mark.parametrize(("method", "url"), ENDPOINTS)
async def test_pimpinan_is_forbidden(
    client: httpx.AsyncClient, pimpinan_headers: dict[str, str], method: str, url: str
) -> None:
    response = await client.request(method, url, headers=pimpinan_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(("method", "url"), ENDPOINTS)
async def test_anonymous_is_unauthorized(client: httpx.AsyncClient, method: str, url: str) -> None:
    response = await client.request(method, url)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_staf_and_admin_can_read_the_log(
    client: httpx.AsyncClient, staf_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    await add_message()

    for headers in (staf_headers, admin_headers):
        assert (await client.get(MESSAGES_URL, headers=headers)).status_code == 200
        assert (await client.get(STATUS_URL, headers=headers)).status_code == 200
