"""Dashboard summary: aggregate counts and amounts, with no personal data in the response."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, insert, select

from app.api.deps import get_clock
from app.db.session import get_engine, get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.settings.model import AppSettings
from app.modules.students.model import Student
from app.shared.clock import Clock

URL = "/api/v1/dashboard/summary"
NOW = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)  # 10:00 WIB, so "today" is 2026-11-02
TODAY = date(2026, 11, 2)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
SEM_A = "Ganjil 2026/2027"
SEM_B = "Genap 2026/2027"

# Strings that identify a person, an invoice or a message: none may appear in a response.
SECRET_NIM = "NIMRAHASIA777"
SECRET_NAME = "Nama Rahasia Lengkap"
SECRET_PHONE = "6281299988877"
SECRET_CODE = "INV-RAHASIA"
SECRET_BODY = "ISI-PESAN-RAHASIA"
SECRETS = (SECRET_NIM, SECRET_NAME, SECRET_PHONE, SECRET_CODE, SECRET_BODY)

INVOICE_KEYS = {
    "total",
    "total_amount",
    "paid",
    "paid_amount",
    "unpaid",
    "unpaid_amount",
    "overdue",
    "overdue_amount",
}
MESSAGE_KEYS = {"queued", "sending", "sent", "delivered", "read", "failed", "cancelled"}
TOP_KEYS = {"invoices", "messages", "warnings", "semester", "generated_at"}
WARNING_KEYS = {"paused_reason", "auto_send_enabled"}


def freeze(app: FastAPI, now: datetime = NOW) -> None:
    clock: Clock = lambda: now  # noqa: E731
    app.dependency_overrides[get_clock] = lambda: clock


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    freeze(app)


# --- Data helpers --------------------------------------------------------------------------

_n = 0


def _next() -> int:
    global _n
    _n += 1
    return _n


async def add_student(*, deleted: bool = False) -> int:
    number = _next()
    async with get_session_factory()() as db:
        student = Student(
            nim=f"{SECRET_NIM}-{number}",
            name=SECRET_NAME,
            phone=SECRET_PHONE,
            phone_status="valid",
            deleted_at=NOW if deleted else None,
        )
        db.add(student)
        await db.commit()
        return student.id


async def add_invoice(
    student_id: int,
    *,
    amount: int,
    due: date,
    semester: str = SEM_A,
    status: str = "unpaid",
    deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        invoice = Invoice(
            invoice_code=f"{SECRET_CODE}-{_next()}",
            student_id=student_id,
            semester=semester,
            amount=amount,
            due_date=due,
            status=status,
            source="siakad",
            deleted_at=NOW if deleted else None,
        )
        db.add(invoice)
        await db.commit()
        return invoice.id


async def add_message(
    student_id: int, invoice_id: int, status: str, *, deleted: bool = False
) -> None:
    async with get_session_factory()() as db:
        db.add(
            Message(
                student_id=student_id,
                invoice_id=invoice_id,
                kind="manual",
                scheduled_date=TODAY,
                body=SECRET_BODY,
                status=status,
                deleted_at=NOW if deleted else None,
            )
        )
        await db.commit()


async def seed() -> None:
    """Semester A: 5 invoices (15M), semester B: 2 invoices (1M), plus rows that must be ignored."""
    student = await add_student()
    paid_a = await add_invoice(student, amount=1_000_000, due=YESTERDAY, status="paid")
    overdue_a1 = await add_invoice(student, amount=2_000_000, due=YESTERDAY)
    await add_invoice(student, amount=3_000_000, due=date(2026, 10, 1))
    due_today = await add_invoice(student, amount=4_000_000, due=TODAY)
    await add_invoice(student, amount=5_000_000, due=TOMORROW)
    overdue_b = await add_invoice(student, amount=700_000, due=date(2026, 10, 5), semester=SEM_B)
    paid_b = await add_invoice(
        student, amount=300_000, due=date(2026, 10, 5), semester=SEM_B, status="paid"
    )

    # Semester A messages: queued 2, sending 1, sent 1, delivered 1, read 1, failed 1, cancelled 1.
    for status in ("queued", "queued", "sending", "sent", "delivered", "read", "failed"):
        await add_message(student, overdue_a1, status)
    await add_message(student, paid_a, "cancelled")
    # Semester B messages: queued 1, failed 2.
    await add_message(student, overdue_b, "queued")
    await add_message(student, paid_b, "failed")
    await add_message(student, overdue_b, "failed")

    # Ignored: a soft-deleted invoice (with a message), a soft-deleted message, a soft-deleted
    # student with an invoice and a message.
    ghost_invoice = await add_invoice(student, amount=9_000_000, due=YESTERDAY, deleted=True)
    await add_message(student, ghost_invoice, "queued")
    await add_message(student, due_today, "queued", deleted=True)
    ghost_student = await add_student(deleted=True)
    ghost_student_invoice = await add_invoice(ghost_student, amount=8_000_000, due=YESTERDAY)
    await add_message(ghost_student, ghost_student_invoice, "queued")


def all_keys(value: Any) -> set[str]:
    """Every dict key anywhere inside a JSON value."""
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys |= all_keys(item)
        return keys
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value)) if value else set()
    return set()


async def summary(
    client: httpx.AsyncClient, headers: dict[str, str], semester: str | None = None
) -> dict[str, Any]:
    params = {"semester": semester} if semester is not None else None
    response = await client.get(URL, headers=headers, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


# --- Counts and amounts --------------------------------------------------------------------


async def test_counts_and_amounts_across_all_semesters(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await seed()

    body = await summary(client, staf_headers)

    assert body["semester"] is None
    assert body["invoices"] == {
        "total": 7,
        "total_amount": 16_000_000,
        "paid": 2,
        "paid_amount": 1_300_000,
        "unpaid": 5,
        "unpaid_amount": 14_700_000,
        "overdue": 3,
        "overdue_amount": 5_700_000,
    }
    assert body["messages"] == {
        "queued": 3,
        "sending": 1,
        "sent": 1,
        "delivered": 1,
        "read": 1,
        "failed": 3,
        "cancelled": 1,
    }
    assert body["warnings"] == {"paused_reason": None, "auto_send_enabled": False}
    assert datetime.fromisoformat(body["generated_at"]) == NOW


async def test_semester_filter_narrows_invoices_and_messages(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await seed()

    a = await summary(client, staf_headers, SEM_A)
    b = await summary(client, staf_headers, SEM_B)

    assert a["semester"] == SEM_A
    assert a["invoices"] == {
        "total": 5,
        "total_amount": 15_000_000,
        "paid": 1,
        "paid_amount": 1_000_000,
        "unpaid": 4,
        "unpaid_amount": 14_000_000,
        "overdue": 2,
        "overdue_amount": 5_000_000,
    }
    assert a["messages"] == {
        "queued": 2,
        "sending": 1,
        "sent": 1,
        "delivered": 1,
        "read": 1,
        "failed": 1,
        "cancelled": 1,
    }
    assert b["semester"] == SEM_B
    assert b["invoices"] == {
        "total": 2,
        "total_amount": 1_000_000,
        "paid": 1,
        "paid_amount": 300_000,
        "unpaid": 1,
        "unpaid_amount": 700_000,
        "overdue": 1,
        "overdue_amount": 700_000,
    }
    assert b["messages"] == {
        "queued": 1,
        "sending": 0,
        "sent": 0,
        "delivered": 0,
        "read": 0,
        "failed": 2,
        "cancelled": 0,
    }


async def test_unknown_semester_yields_zeros(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await seed()

    body = await summary(client, staf_headers, "Tidak Ada")

    assert body["invoices"] == dict.fromkeys(INVOICE_KEYS, 0)
    assert body["messages"] == dict.fromkeys(MESSAGE_KEYS, 0)


async def test_empty_database_yields_zeros_never_null(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    body = await summary(client, staf_headers)

    assert body["invoices"] == dict.fromkeys(INVOICE_KEYS, 0)
    assert body["messages"] == dict.fromkeys(MESSAGE_KEYS, 0)
    assert body["warnings"] == {"paused_reason": None, "auto_send_enabled": False}


async def test_warnings_reflect_the_settings(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    async with get_session_factory()() as db:
        db.add(AppSettings(id=1, auto_send_enabled=True, paused_reason="circuit_breaker"))
        await db.commit()

    body = await summary(client, staf_headers)

    assert body["warnings"] == {"paused_reason": "circuit_breaker", "auto_send_enabled": True}


async def test_summary_is_repeatable(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await seed()

    first = await summary(client, staf_headers)
    second = await summary(client, staf_headers)

    assert first == second


# --- Overdue boundary ----------------------------------------------------------------------


async def test_overdue_boundary_due_today_is_not_overdue(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student = await add_student()
    await add_invoice(student, amount=100, due=TODAY)
    await add_invoice(student, amount=200, due=YESTERDAY)
    await add_invoice(student, amount=400, due=YESTERDAY, status="paid")  # paid is never overdue
    await add_invoice(student, amount=800, due=TOMORROW)

    invoices = (await summary(client, staf_headers))["invoices"]

    assert invoices["overdue"] == 1
    assert invoices["overdue_amount"] == 200
    assert invoices["unpaid"] == 3
    assert invoices["paid"] == 1


async def test_overdue_uses_the_jakarta_date_around_midnight(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student = await add_student()
    await add_invoice(student, amount=100, due=TODAY)  # due 2026-11-02

    # 16:30Z is 23:30 WIB on 2026-11-02: due today, not overdue yet.
    freeze(app, datetime(2026, 11, 2, 16, 30, tzinfo=UTC))
    assert (await summary(client, staf_headers))["invoices"]["overdue"] == 0

    # 17:30Z is 00:30 WIB on 2026-11-03: the same invoice flipped to overdue.
    freeze(app, datetime(2026, 11, 2, 17, 30, tzinfo=UTC))
    invoices = (await summary(client, staf_headers))["invoices"]
    assert invoices["overdue"] == 1
    assert invoices["overdue_amount"] == 100


# --- Privacy -------------------------------------------------------------------------------


async def test_response_contains_no_personal_data(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await seed()
    async with get_session_factory()() as db:
        db.add(AppSettings(id=1, paused_reason="circuit_breaker"))
        await db.commit()

    response = await client.get(URL, headers=staf_headers, params={"semester": SEM_A})

    assert response.status_code == 200
    for secret in SECRETS:
        assert secret not in response.text
    body = response.json()
    assert all_keys(body) == TOP_KEYS | INVOICE_KEYS | MESSAGE_KEYS | WARNING_KEYS
    assert set(body["invoices"]) == INVOICE_KEYS
    assert set(body["messages"]) == MESSAGE_KEYS
    assert set(body["warnings"]) == WARNING_KEYS


# --- Validation ----------------------------------------------------------------------------


async def test_semester_longer_than_20_characters_is_rejected(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    ok = await client.get(URL, headers=staf_headers, params={"semester": "x" * 20})
    too_long = await client.get(URL, headers=staf_headers, params={"semester": "x" * 21})

    assert ok.status_code == 200
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"]


# --- Permissions ---------------------------------------------------------------------------


async def test_every_role_with_dashboard_read_is_allowed(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    staf_headers: dict[str, str],
    pimpinan_headers: dict[str, str],
) -> None:
    for headers in (admin_headers, staf_headers, pimpinan_headers):
        assert (await client.get(URL, headers=headers)).status_code == 200


async def test_anonymous_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.get(URL)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


# --- Scale ---------------------------------------------------------------------------------


async def test_summary_uses_a_handful_of_statements_for_hundreds_of_rows(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    count = 400
    async with get_session_factory()() as db:
        await db.execute(
            insert(Student),
            [
                {"nim": f"N{i:04d}", "name": f"Mahasiswa {i}", "phone_status": "missing"}
                for i in range(count)
            ],
        )
        await db.commit()
    async with get_session_factory()() as db:
        ids = list((await db.scalars(select(Student.id).order_by(Student.id))).all())
        await db.execute(
            insert(Invoice),
            [
                {
                    "invoice_code": f"INV-{i:04d}",
                    "student_id": ids[i],
                    "semester": SEM_A if i % 2 else SEM_B,
                    "amount": 1_000_000,
                    "due_date": YESTERDAY if i % 3 else TOMORROW,
                    "status": "unpaid" if i % 5 else "paid",
                    "source": "siakad",
                }
                for i in range(count)
            ],
        )
        invoice_ids = list((await db.scalars(select(Invoice.id).order_by(Invoice.id))).all())
        await db.execute(
            insert(Message),
            [
                {
                    "student_id": ids[i],
                    "invoice_id": invoice_ids[i],
                    "kind": "manual",
                    "scheduled_date": TODAY,
                    "body": "x",
                    "status": ("queued", "sent", "failed")[i % 3],
                    "attempts": 0,
                }
                for i in range(count)
            ],
        )
        await db.commit()

    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        body = await summary(client, staf_headers, SEM_A)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)

    assert body["invoices"]["total"] == count // 2
    assert sum(body["messages"].values()) == count // 2
    # Authentication needs a few statements of its own; the summary adds a handful more.
    assert len(statements) <= 12, f"{len(statements)} statements for {count} rows"
