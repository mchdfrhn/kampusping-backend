import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.audit.model import AuditEvent
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.students.model import Student
from app.modules.whatsapp.signature import sign_body
from app.shared.utils import ensure_utc

MESSAGES_URL = "/api/v1/messages"
STATUS_URL = "/api/v1/whatsapp/status"
LOGOUT_URL = "/api/v1/whatsapp/logout"
WEBHOOK_URL = "/api/v1/webhooks/whatsapp"
SECRET = "test-webhook-secret"
DAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
FULL_PHONE = "+6281234567890"
BODY = "Yth. Budi, tagihan UKT Anda jatuh tempo.\n\nBalas STOP untuk berhenti."

# --- Data helpers --------------------------------------------------------------------------

_counter: Iterator[int] = iter(range(1, 10_000))


async def add_student(*, nim: str | None = None, name: str = "Budi Santoso") -> int:
    number = next(_counter)
    async with get_session_factory()() as db:
        student = Student(
            nim=nim or f"2021{number:04d}", name=name, phone=FULL_PHONE, phone_status="valid"
        )
        db.add(student)
        await db.commit()
        return student.id


async def add_message(
    student_id: int | None = None,
    *,
    scheduled: date = DAY,
    status: str = "queued",
    **values: Any,
) -> int:
    student_id = student_id or await add_student()
    number = next(_counter)
    async with get_session_factory()() as db:
        invoice = Invoice(
            invoice_code=f"INV-{number}",
            student_id=student_id,
            semester="Ganjil 2026/2027",
            amount=1_250_000,
            due_date=scheduled,
            status="unpaid",
            source="siakad",
        )
        db.add(invoice)
        await db.flush()
        message = Message(
            student_id=student_id,
            invoice_id=invoice.id,
            kind="auto",
            scheduled_date=scheduled,
            body=BODY,
            status=status,
            queued_at=NOW - timedelta(hours=number),
            **values,
        )
        db.add(message)
        await db.commit()
        return message.id


async def get_message(message_id: int) -> Message:
    async with get_session_factory()() as db:
        message = await db.get(Message, message_id)
        assert message is not None
        return message


def utc(value: datetime | None) -> datetime:
    assert value is not None
    return ensure_utc(value)


async def audit_events(action: str) -> list[AuditEvent]:
    async with get_session_factory()() as db:
        rows = await db.scalars(select(AuditEvent).where(AuditEvent.action == action))
        return list(rows.all())


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


async def post_event(
    client: httpx.AsyncClient, event: Any, *, secret: str = SECRET
) -> httpx.Response:
    body = json.dumps(event).encode()
    return await send_raw(client, body, signature=sign_body(secret, body))


async def send_raw(
    client: httpx.AsyncClient, body: bytes, *, signature: str | None
) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature
    return await client.post(WEBHOOK_URL, content=body, headers=headers)
