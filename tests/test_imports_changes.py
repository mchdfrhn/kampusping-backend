"""The per-row change log written when an import is applied (the basis of rollback)."""

from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.imports.changes import deserialize_value
from app.modules.imports.model import ImportBatch, ImportChange
from app.modules.invoices.model import Invoice

from .imports_helpers import import_lines, invoice_by_code, line


async def _make_batch() -> int:
    async with get_session_factory()() as db:
        batch = ImportBatch(source="siakad", file_name="x.csv", summary={"rows_total": 0})
        db.add(batch)
        await db.commit()
        return batch.id


async def test_import_change_roundtrip() -> None:
    batch_id = await _make_batch()
    async with get_session_factory()() as db:
        db.add(
            ImportChange(
                batch_id=batch_id,
                entity="invoice",
                entity_id=7,
                action="updated",
                before={"amount": 1},
                after={"amount": 2},
            )
        )
        await db.commit()

    async with get_session_factory()() as db:
        change = await db.scalar(select(ImportChange).where(ImportChange.batch_id == batch_id))
        assert change is not None
        assert change.entity == "invoice"
        assert change.entity_id == 7
        assert change.action == "updated"
        assert change.before == {"amount": 1}
        assert change.after == {"amount": 2}


async def test_batch_defaults() -> None:
    batch_id = await _make_batch()
    async with get_session_factory()() as db:
        batch = await db.get(ImportBatch, batch_id)
        assert batch is not None
        assert batch.has_change_log is False
        assert batch.rolled_back_at is None


# --- recording on apply -----------------------------------------------------------------------


async def _changes(batch_id: int, entity: str | None = None) -> list[ImportChange]:
    async with get_session_factory()() as db:
        stmt = (
            select(ImportChange).where(ImportChange.batch_id == batch_id).order_by(ImportChange.id)
        )
        if entity is not None:
            stmt = stmt.where(ImportChange.entity == entity)
        return list((await db.scalars(stmt)).all())


async def _has_change_log(batch_id: int) -> bool:
    async with get_session_factory()() as db:
        batch = await db.get(ImportBatch, batch_id)
        assert batch is not None
        return batch.has_change_log


async def test_apply_records_created_students_and_invoices(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await import_lines(
        client,
        staf_headers,
        line(nim="2201", code="INV-1", amount="5000000"),
        line(nim="2202", name="Siti", code="INV-2", amount="3000000"),
    )

    students = await _changes(batch["id"], "student")
    invoices = await _changes(batch["id"], "invoice")
    assert len(students) == 2
    assert len(invoices) == 2
    assert {c.action for c in students + invoices} == {"created"}
    assert all(c.before is None for c in students + invoices)
    amounts = {c.after["amount"] for c in invoices}
    assert amounts == {5_000_000, 3_000_000}
    assert {c.entity_id for c in invoices} == {
        (await invoice_by_code("INV-1")).id,
        (await invoice_by_code("INV-2")).id,
    }
    assert await _has_change_log(batch["id"]) is True


async def test_apply_records_only_changed_fields_for_updates(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(code="INV-1", amount="5000000"))
    second = await import_lines(client, staf_headers, line(code="INV-1", amount="6000000"))

    changes = await _changes(second["id"])
    assert [(c.entity, c.action) for c in changes] == [("invoice", "updated")]
    assert changes[0].before == {"amount": 5_000_000}
    assert changes[0].after == {"amount": 6_000_000}


async def test_apply_records_paid_transition(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(code="INV-1"))
    second = await import_lines(
        client, staf_headers, line(code="INV-1", status="lunas", paid_at="2026-11-02")
    )

    (change,) = await _changes(second["id"], "invoice")
    assert change.action == "updated"
    assert change.before == {"status": "unpaid", "paid_at": None, "paid_via": None}
    assert change.after == {"status": "paid", "paid_at": "2026-11-02", "paid_via": "siakad"}


async def test_apply_records_superseded_invoice(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    second = await import_lines(client, staf_headers, line(nim="2201", code="INV-2"))

    invoices = await _changes(second["id"], "invoice")
    by_action = {c.action: c for c in invoices}
    assert set(by_action) == {"created", "updated"}
    assert by_action["created"].entity_id == (await invoice_by_code("INV-2")).id
    superseded = by_action["updated"]
    assert superseded.entity_id == (await invoice_by_code("INV-1")).id
    assert superseded.before is not None
    assert superseded.before["status"] == "unpaid"
    assert superseded.after["status"] == "paid"
    assert await _changes(second["id"], "student") == []


async def test_apply_records_restored_soft_deleted_row(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(code="INV-1"))
    async with get_session_factory()() as db:
        invoice = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-1"))
        assert invoice is not None
        invoice.deleted_at = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
        invoice.deleted_by = "tester"
        await db.commit()

    second = await import_lines(client, staf_headers, line(code="INV-1"))

    (change,) = await _changes(second["id"], "invoice")
    assert change.action == "updated"
    assert change.before is not None
    assert isinstance(change.before["deleted_at"], str)
    assert change.before["deleted_by"] == "tester"
    assert change.after == {"deleted_at": None, "deleted_by": None}


async def test_sticky_skip_and_unchanged_rows_record_nothing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(
        client, staf_headers, line(code="INV-1", status="lunas", paid_at="2026-11-02")
    )
    sticky = await import_lines(client, staf_headers, line(code="INV-1", status="belum lunas"))
    same = await import_lines(client, staf_headers, line(code="INV-1", status="lunas"))

    assert await _changes(sticky["id"]) == []
    assert await _changes(same["id"]) == []
    assert await _has_change_log(sticky["id"]) is True


async def test_entity_touched_by_two_rows_is_one_change(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", name="Budi", code="INV-1"))
    second = await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi Baru", code="INV-2"),
        line(nim="2201", name="Budi Terbaru", code="INV-3"),
    )

    (student,) = await _changes(second["id"], "student")
    assert student.before == {"name": "Budi"}
    assert student.after == {"name": "Budi Terbaru"}


def test_serialization_roundtrip() -> None:
    assert deserialize_value("due_date", "2026-11-15") == date(2026, 11, 15)
    assert deserialize_value("paid_at", None) is None
    assert deserialize_value("deleted_at", None) is None
    assert deserialize_value("deleted_at", "2026-11-05T03:00:00+00:00") == datetime(
        2026, 11, 5, 3, 0, tzinfo=UTC
    )
    assert deserialize_value("amount", 5) == 5
