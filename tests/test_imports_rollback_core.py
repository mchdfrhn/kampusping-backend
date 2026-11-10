"""Rollback plan (conflicts, counts) and execution over the import change log."""

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql

from app.db.session import get_session_factory
from app.modules.imports.rollback import (
    RollbackConflict,
    RollbackPlan,
    build_plan,
    entities_query,
    execute_plan,
)
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.students.model import Student

from .imports_helpers import import_lines, invoice_by_code, line

ACTOR = "admin@example.com"
NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


async def _snapshot() -> dict[str, Any]:
    """Business values of every row that is not soft-deleted."""
    async with get_session_factory()() as db:
        students = await db.scalars(select(Student).where(Student.deleted_at.is_(None)))
        invoices = await db.scalars(select(Invoice).where(Invoice.deleted_at.is_(None)))
        return {
            "students": {
                s.nim: (s.name, s.study_program, s.cohort, s.phone, s.phone_status)
                for s in students
            },
            "invoices": {
                i.invoice_code: (
                    i.student_id,
                    i.semester,
                    i.amount,
                    i.due_date,
                    i.status,
                    i.paid_at,
                    i.paid_via,
                    i.source,
                )
                for i in invoices
            },
        }


async def _plan(batch_id: int) -> RollbackPlan:
    async with get_session_factory()() as db:
        return await build_plan(db, batch_id)


async def _rollback(batch_id: int) -> RollbackPlan:
    async with get_session_factory()() as db:
        plan = await build_plan(db, batch_id)
        await execute_plan(db, plan, actor=ACTOR, now=NOW)
        await db.commit()
        return plan


async def _two_batches(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[int, int]:
    first = await import_lines(
        client,
        headers,
        line(nim="2201", name="Budi", code="INV-1", amount="5000000"),
        line(nim="2202", name="Siti", code="INV-2", amount="3000000"),
    )
    second = await import_lines(
        client,
        headers,
        line(nim="2201", name="Budi", code="INV-1", amount="6000000"),
        line(nim="2203", name="Andi", code="INV-3", amount="2000000"),
    )
    return first["id"], second["id"]


async def test_plan_counts_created_and_reverted(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    _, second = await _two_batches(client, staf_headers)

    plan = await _plan(second)

    assert plan.students_deleted == 1
    assert plan.invoices_deleted == 1
    assert plan.invoices_reverted == 1
    assert plan.students_reverted == 0
    assert plan.conflicts == []


async def test_execute_restores_exact_previous_state(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi", code="INV-1", amount="5000000"),
        line(nim="2202", name="Siti", code="INV-2", amount="3000000"),
    )
    before_second = await _snapshot()
    second = await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi", code="INV-1", amount="6000000", status="lunas"),
        line(nim="2202", name="Siti Baru", code="INV-2", amount="3000000", due="2026-12-01"),
        line(nim="2203", name="Andi", code="INV-3", amount="2000000"),
    )
    assert await _snapshot() != before_second

    await _rollback(second["id"])

    assert await _snapshot() == before_second
    assert first["id"] != second["id"]
    async with get_session_factory()() as db:
        andi = await db.scalar(select(Student).where(Student.nim == "2203"))
        assert andi is not None
        assert andi.deleted_at is not None
        assert andi.deleted_by == ACTOR


async def test_manual_change_after_import_is_a_conflict(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    async with get_session_factory()() as db:
        invoice = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-1"))
        assert invoice is not None
        invoice.status = "paid"
        await db.commit()

    plan = await _plan(batch["id"])

    assert plan.conflicts == [RollbackConflict("invoice", "INV-1", ["status"])]


async def test_soft_deleted_created_invoice_is_a_conflict(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    async with get_session_factory()() as db:
        invoice = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-1"))
        assert invoice is not None
        invoice.deleted_at = NOW
        invoice.deleted_by = "someone"
        await db.commit()

    plan = await _plan(batch["id"])

    assert plan.conflicts == [RollbackConflict("invoice", "INV-1", ["deleted_at", "deleted_by"])]


async def test_queued_messages_are_cancelled_sent_ones_untouched(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    invoice = await invoice_by_code("INV-1")
    ids: dict[str, int] = {}
    async with get_session_factory()() as db:
        for status in ("queued", "sent"):
            message = Message(
                student_id=invoice.student_id,
                invoice_id=invoice.id,
                kind="manual",
                scheduled_date=NOW.date(),
                body="Halo",
                status=status,
            )
            db.add(message)
            await db.flush()
            ids[status] = message.id
        await db.commit()

    plan = await _rollback(batch["id"])

    assert plan.queued_messages == 1
    assert plan.sent_messages == 1
    async with get_session_factory()() as db:
        queued = await db.get(Message, ids["queued"])
        sent = await db.get(Message, ids["sent"])
        assert queued is not None
        assert queued.status == "cancelled"
        assert queued.cancel_reason == "manual"
        assert queued.error == "Impor dibatalkan"
        assert sent is not None
        assert sent.status == "sent"


async def test_large_import_is_processed_in_chunks(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    rows = [line(nim=f"3{i:04d}", name=f"Mhs {i}", code=f"BULK-{i}", phone="") for i in range(600)]
    batch = await import_lines(client, staf_headers, *rows)

    plan = await _rollback(batch["id"])

    assert plan.students_deleted == 600
    assert plan.invoices_deleted == 600
    async with get_session_factory()() as db:
        students = await db.scalar(
            select(func.count()).select_from(Student).where(Student.deleted_at.is_not(None))
        )
        invoices = await db.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.deleted_at.is_not(None))
        )
    assert (students, invoices) == (600, 600)


async def test_restored_row_is_reverted_to_deleted(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    async with get_session_factory()() as db:
        invoice = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-1"))
        assert invoice is not None
        invoice.deleted_at = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
        invoice.deleted_by = "tester"
        await db.commit()
    second = await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    assert (await invoice_by_code("INV-1")).deleted_at is None

    await _rollback(second["id"])

    restored = await invoice_by_code("INV-1")
    assert restored.deleted_at is not None
    assert restored.deleted_by == "tester"


async def test_worker_marking_a_phone_invalid_is_not_a_conflict(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    async with get_session_factory()() as db:
        student = await db.scalar(select(Student).where(Student.nim == "2201"))
        assert student is not None
        student.phone_status = "invalid"  # what the sender does after a "not on WhatsApp" failure
        await db.commit()

    assert (await _plan(batch["id"])).conflicts == []


async def test_a_hard_deleted_row_is_skipped_not_a_conflict(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await import_lines(
        client,
        staf_headers,
        line(nim="2201", code="INV-1"),
        line(nim="2202", name="Siti", code="INV-2"),
    )
    async with get_session_factory()() as db:
        await db.execute(delete(Invoice).where(Invoice.invoice_code == "INV-1"))
        await db.commit()

    plan = await _rollback(batch["id"])

    assert plan.conflicts == []
    assert (await invoice_by_code("INV-2")).deleted_at is not None


def test_locked_entity_query_uses_for_update_on_postgresql() -> None:
    dialect = postgresql.dialect()
    assert "FOR UPDATE" in str(entities_query(Invoice, [1, 2], lock=True).compile(dialect=dialect))
    assert "FOR UPDATE" not in str(
        entities_query(Invoice, [1, 2], lock=False).compile(dialect=dialect)
    )
