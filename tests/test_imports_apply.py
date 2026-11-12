import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import AppException
from app.db.base import Base
from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch
from app.modules.imports.service import ImportService
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.students.model import Student
from app.modules.users.model import User

from .imports_helpers import (
    IMPORTS_URL,
    SEMESTER,
    apply,
    count,
    import_lines,
    invoice_by_code,
    line,
    make_csv,
    preview,
    rows_are_sql_null,
    stored_rows,
    student_by_nim,
)
from .test_students_invoices import audit_events, freeze_clock, make_invoice, make_student

# --- apply ------------------------------------------------------------------------------------


async def test_apply_upserts_students_and_invoices(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi", code="INV-1", amount="5000000"),
        line(nim="2202", name="Siti", code="INV-2", amount="3000000"),
    )

    assert first["status"] == "applied"
    assert first["applied_at"] is not None
    assert first["summary"] == {
        "rows_total": 2,
        "rows_valid": 2,
        "rows_error": 0,
        "students_new": 2,
        "students_updated": 0,
        "invoices_new": 2,
        "invoices_updated": 0,
        "invoices_skipped": 0,
    }
    student = await student_by_nim("2201")
    assert student.name == "Budi"
    assert student.study_program == "Teknik Informatika"
    assert student.cohort == "2022"
    assert student.phone == "+6281234567890"
    assert student.phone_status == "valid"
    assert student.created_by == "staf@example.com"
    invoice = await invoice_by_code("INV-1")
    assert invoice.amount == 5_000_000
    assert invoice.status == "unpaid"
    assert invoice.source == "siakad"
    assert invoice.semester == SEMESTER
    assert invoice.due_date == date(2026, 11, 15)
    invoice_id = invoice.id

    second = await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi S.", code="INV-1", amount="6500000", due="2026-12-01"),
        source="sipema",
    )

    assert second["summary"]["students_new"] == 0
    assert second["summary"]["students_updated"] == 1
    assert second["summary"]["invoices_new"] == 0
    assert second["summary"]["invoices_updated"] == 1
    updated = await invoice_by_code("INV-1")
    assert updated.id == invoice_id
    assert updated.amount == 6_500_000
    assert updated.due_date == date(2026, 12, 1)
    assert updated.source == "sipema"
    assert updated.updated_by == "staf@example.com"
    assert (await student_by_nim("2201")).name == "Budi S."
    assert await count(Student) == 2
    assert await count(Invoice) == 2
    assert len(await audit_events("import.applied")) == 2


async def test_apply_keeps_student_fields_when_the_row_leaves_them_empty(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", name="Budi", code="INV-1"))

    await import_lines(
        client, staf_headers, line(nim="2201", name="", prodi="", cohort="", code="INV-1")
    )

    student = await student_by_nim("2201")
    assert student.name == "Budi"
    assert student.study_program == "Teknik Informatika"
    assert student.cohort == "2022"


async def test_paid_is_sticky(client: httpx.AsyncClient, staf_headers: dict[str, str]) -> None:
    await import_lines(
        client,
        staf_headers,
        line(code="INV-1", status="lunas", paid_at="2026-10-20", via="siakad"),
    )

    second = await import_lines(
        client, staf_headers, line(code="INV-1", status="belum lunas", amount="7000000")
    )

    invoice = await invoice_by_code("INV-1")
    assert invoice.status == "paid"
    assert invoice.paid_at == date(2026, 10, 20)
    assert invoice.paid_via == "siakad"
    assert invoice.amount == 7_000_000  # other fields still update
    assert second["summary"]["invoices_skipped"] == 1
    assert second["summary"]["invoices_updated"] == 0
    assert second["summary"]["invoices_new"] == 0


async def test_preview_predicts_sticky_skip(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    await make_invoice(student_id, "INV-1", status="paid", paid_via="sipema")

    body = await preview(client, staf_headers, line(code="INV-1", status="belum lunas"))

    assert body["summary"]["invoices_skipped"] == 1
    assert body["summary"]["invoices_updated"] == 0


async def test_paid_row_sets_paid_at_and_paid_via(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    # 18:00 UTC is already 1 November in Jakarta.
    freeze_clock(app, datetime(2026, 10, 31, 18, 0, tzinfo=UTC))

    await import_lines(
        client,
        staf_headers,
        line(nim="2201", code="INV-1", status="lunas"),  # no date, no channel
        line(
            nim="2202",
            name="Siti",
            code="INV-2",
            status="Lunas",
            paid_at="2026-10-05",
            via="siakad",
        ),
        line(nim="2203", name="Rudi", code="INV-3", status="belum lunas"),
        source="sipema",
    )

    first = await invoice_by_code("INV-1")
    assert first.status == "paid"
    assert first.paid_at == date(2026, 11, 1)
    assert first.paid_via == "sipema"  # falls back to the batch source
    second = await invoice_by_code("INV-2")
    assert second.paid_at == date(2026, 10, 5)
    assert second.paid_via == "siakad"
    third = await invoice_by_code("INV-3")
    assert (third.status, third.paid_at, third.paid_via) == ("unpaid", None, None)


async def test_unpaid_invoice_turning_paid_in_a_later_file(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(code="INV-1"))

    second = await import_lines(
        client, staf_headers, line(code="INV-1", status="lunas", paid_at="2026-11-02")
    )

    invoice = await invoice_by_code("INV-1")
    assert (invoice.status, invoice.paid_at, invoice.paid_via) == (
        "paid",
        date(2026, 11, 2),
        "siakad",
    )
    assert second["summary"]["invoices_updated"] == 1


async def test_valid_phone_not_overwritten_by_invalid(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", code="INV-1", phone="081234567890"))

    await import_lines(client, staf_headers, line(nim="2201", code="INV-1", phone="12345"))

    student = await student_by_nim("2201")
    assert student.phone == "+6281234567890"
    assert student.phone_status == "valid"


async def test_valid_phone_in_file_replaces_the_stored_one(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", code="INV-1", phone="081234567890"))

    await import_lines(client, staf_headers, line(nim="2201", code="INV-1", phone="0857-1111-2222"))

    student = await student_by_nim("2201")
    assert student.phone == "+6285711112222"
    assert student.phone_status == "valid"


async def test_unusable_phone_marks_a_student_without_a_valid_phone_invalid(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(
        client,
        staf_headers,
        line(nim="2201", code="INV-1", phone="12345"),  # new student, bad number
        line(nim="2202", name="Siti", code="INV-2", phone=""),  # new student, no number
    )
    assert (await student_by_nim("2201")).phone is None
    assert (await student_by_nim("2201")).phone_status == "invalid"
    assert (await student_by_nim("2202")).phone_status == "missing"

    # Existing student stored as missing gets an unusable number: invalid, not valid.
    await make_student("2203", "Rudi", phone=None, phone_status="missing")
    await import_lines(client, staf_headers, line(nim="2203", name="", code="INV-3", phone="abc"))
    assert (await student_by_nim("2203")).phone_status == "invalid"


async def test_missing_phone_marks_student_missing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2201", "Budi", phone="+6281234567890", phone_status="valid")
    await make_student("2202", "Siti", phone=None, phone_status="missing")

    await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="", code="INV-1", phone=""),
        line(nim="2202", name="", code="INV-2", phone=""),
        line(nim="2203", name="Rudi", code="INV-3", phone=""),
    )

    assert (await student_by_nim("2201")).phone == "+6281234567890"  # existing number is kept
    assert (await student_by_nim("2201")).phone_status == "valid"
    assert (await student_by_nim("2202")).phone_status == "missing"
    assert (await student_by_nim("2203")).phone is None
    assert (await student_by_nim("2203")).phone_status == "missing"


async def test_import_never_changes_opted_out_at(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    opted_out = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    student_id = await make_student("2201", "Budi")
    async with get_session_factory()() as db:
        student = await db.get(Student, student_id)
        assert student is not None
        student.opted_out_at = opted_out
        student.opted_out_reason = "Minta berhenti"
        await db.commit()

    await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi Baru", code="INV-1", phone="0857-1111-2222"),
    )

    student = await student_by_nim("2201")
    assert student.name == "Budi Baru"
    assert student.opted_out_at is not None
    assert student.opted_out_at.replace(tzinfo=UTC) == opted_out
    assert student.opted_out_reason == "Minta berhenti"


async def test_new_invoice_auto_marks_old_unpaid_invoice_paid_and_cancels_queued_messages(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    freeze_clock(app, datetime(2026, 11, 5, 3, 0, tzinfo=UTC))
    student_id = await make_student("2201", "Budi")
    old_id = await make_invoice(student_id, "INV-OLD", amount=1_234_000, status="unpaid")
    await make_invoice(
        student_id, "INV-ALREADY-PAID", amount=900_000, status="paid", paid_via="sipema"
    )
    other_student_id = await make_student("2202", "Siti")
    await make_invoice(other_student_id, "INV-OTHER", amount=2_000_000, status="unpaid")
    before_other = await invoice_by_code("INV-OTHER")

    async with get_session_factory()() as db:
        db.add(
            Message(
                student_id=student_id,
                invoice_id=old_id,
                rule_id=None,
                kind="manual",
                scheduled_date=date(2026, 11, 5),
                body="Tagihan lama",
                status="queued",
            )
        )
        await db.commit()

    previewed = await preview(client, staf_headers, line(nim="2201", name="", code="INV-NEW"))
    assert previewed["summary"]["invoices_new"] == 1
    assert previewed["summary"]["invoices_updated"] == 1

    applied = (await apply(client, staf_headers, previewed["id"])).json()
    assert applied["summary"]["invoices_new"] == 1
    assert applied["summary"]["invoices_updated"] == 1

    after_old = await invoice_by_code("INV-OLD")
    assert after_old.amount == 1_234_000
    assert after_old.status == "paid"
    assert after_old.paid_at == date(2026, 11, 5)
    assert after_old.paid_via == "siakad"
    assert after_old.deleted_at is None

    after_new = await invoice_by_code("INV-NEW")
    assert after_new.status == "unpaid"

    # Queued message on the old invoice is automatically cancelled with reason 'paid'.
    async with get_session_factory()() as db:
        msg = await db.scalar(select(Message).where(Message.invoice_id == old_id))
        assert msg is not None
        assert msg.status == "cancelled"
        assert msg.cancel_reason == "paid"

    # Unlisted invoice of another student not in the file remains untouched.
    after_other = await invoice_by_code("INV-OTHER")
    assert after_other.status == "unpaid"
    assert after_other.updated_at == before_other.updated_at
    assert await count(Invoice) == 4


async def test_apply_restores_soft_deleted_student_and_invoice(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    await make_invoice(student_id, "INV-1", amount=1_000_000)
    async with get_session_factory()() as db:
        for model in (Student, Invoice):
            row = (await db.scalars(select(model))).one()
            row.deleted_at = datetime(2026, 10, 1, tzinfo=UTC)
            row.deleted_by = "admin@example.com"
        await db.commit()

    applied = await import_lines(
        client, staf_headers, line(nim="2201", name="", code="INV-1", amount="2000000")
    )

    assert applied["summary"]["students_new"] == 0
    assert applied["summary"]["invoices_new"] == 0
    assert await count(Student) == 1  # restored, not duplicated
    assert await count(Invoice) == 1
    student = await student_by_nim("2201")
    invoice = await invoice_by_code("INV-1")
    assert student.deleted_at is None
    assert student.deleted_by is None
    assert invoice.deleted_at is None
    assert invoice.amount == 2_000_000


async def test_apply_clears_the_stored_rows(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line())
    rows = await stored_rows(batch["id"])
    assert rows is not None
    assert rows[0]["due_date"] == "2026-11-15"  # dates are ISO strings

    await apply(client, staf_headers, batch["id"])

    assert await rows_are_sql_null(batch["id"])


async def test_apply_twice_returns_409_and_creates_nothing_new(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(
        client, staf_headers, line(code="INV-1"), line(nim="2202", name="S", code="INV-2")
    )
    first = await apply(client, staf_headers, batch["id"])
    assert first.status_code == 200
    students, invoices = await count(Student), await count(Invoice)

    second = await apply(client, staf_headers, batch["id"])

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    assert second.json()["error"]["message"]
    assert await count(Student) == students
    assert await count(Invoice) == invoices
    assert len(await audit_events("import.applied")) == 1
    after = (await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)).json()
    assert after["status"] == "applied"
    assert after["summary"] == first.json()["summary"]


async def test_apply_discarded_batch_409(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line())

    discarded = await client.post(f"{IMPORTS_URL}/{batch['id']}/discard", headers=staf_headers)
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "discarded"

    response = await apply(client, staf_headers, batch["id"])

    assert response.status_code == 409
    assert await count(Student) == 0
    assert await count(Invoice) == 0
    again = await client.post(f"{IMPORTS_URL}/{batch['id']}/discard", headers=staf_headers)
    assert again.status_code == 409
    assert len(await audit_events("import.discarded")) == 1
    assert await rows_are_sql_null(batch["id"])


async def test_discard_applied_batch_409(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    applied = await import_lines(client, staf_headers, line())

    response = await client.post(f"{IMPORTS_URL}/{applied['id']}/discard", headers=staf_headers)

    assert response.status_code == 409
    after = (await client.get(f"{IMPORTS_URL}/{applied['id']}", headers=staf_headers)).json()
    assert after["status"] == "applied"


async def test_apply_and_discard_unknown_batch_404(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    assert (await apply(client, staf_headers, 999)).status_code == 404
    assert (
        await client.post(f"{IMPORTS_URL}/999/discard", headers=staf_headers)
    ).status_code == 404
    assert (await client.get(f"{IMPORTS_URL}/999", headers=staf_headers)).status_code == 404
    assert (await client.get(f"{IMPORTS_URL}/999/errors", headers=staf_headers)).status_code == 404
    assert (await client.delete(f"{IMPORTS_URL}/999", headers=staf_headers)).status_code == 404


async def test_delete_preview_batch_204(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line())
    delete_res = await client.delete(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)
    assert delete_res.status_code == 204

    # Disappears from single GET and listing
    res = await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)
    assert res.status_code == 404
    history = (await client.get(IMPORTS_URL, headers=staf_headers)).json()
    assert not any(item["id"] == batch["id"] for item in history["items"])

    # Audit event recorded
    events = await audit_events("import.deleted")
    assert len(events) == 1
    assert events[0].entity_id == str(batch["id"])


async def test_delete_discarded_batch_204(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line())
    await client.post(f"{IMPORTS_URL}/{batch['id']}/discard", headers=staf_headers)

    delete_res = await client.delete(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)
    assert delete_res.status_code == 204
    res = await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)
    assert res.status_code == 404


async def test_delete_applied_batch_409(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    applied = await import_lines(client, staf_headers, line())
    delete_res = await client.delete(f"{IMPORTS_URL}/{applied['id']}", headers=staf_headers)
    assert delete_res.status_code == 409
    assert "tidak dapat dihapus" in delete_res.json()["error"]["message"]


async def test_failed_apply_leaves_batch_in_preview_and_changes_nothing(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = await preview(client, staf_headers, line(code="INV-1"))

    async def failing_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit store down")

    with monkeypatch.context() as patched:
        patched.setattr("app.modules.imports.service.record_audit", failing_audit)
        with pytest.raises(RuntimeError):
            await apply(client, staf_headers, batch["id"])

    assert await count(Student) == 0
    assert await count(Invoice) == 0
    after = (await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)).json()
    assert after["status"] == "preview"
    assert after["applied_at"] is None
    retry = await apply(client, staf_headers, batch["id"])
    assert retry.status_code == 200
    assert await count(Invoice) == 1


async def test_concurrent_applies_have_exactly_one_winner(tmp_path: Path) -> None:
    """Two sessions race to apply the same preview; a file database gives each its own connection.

    The in-memory test database shares one connection between sessions, so a race cannot be
    exercised through the HTTP client; this drives the service directly instead.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    actor = User(email="staf@example.com", full_name="Staf", password_hash="x")
    try:
        async with factory() as db:
            batch = await ImportService(db).preview(
                "tagihan.csv",
                make_csv(line(code="INV-1"), line(nim="2202", name="Siti", code="INV-2")),
                source="siakad",
                semester=SEMESTER,
                actor=actor,
            )
            batch_id = batch.id

        async def attempt() -> object:
            async with factory() as db:
                return await ImportService(db).apply(batch_id, actor)

        results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

        failures = [r for r in results if isinstance(r, AppException)]
        winners = [r for r in results if isinstance(r, ImportBatch)]
        assert len(winners) == 1, results
        assert len(failures) == 1, results
        assert failures[0].status_code == 409
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(Student)) == 2
            assert await db.scalar(select(func.count()).select_from(Invoice)) == 2
    finally:
        await engine.dispose()


# --- invoice codes that belong to another student ----------------------------------------------


async def seed_owner_scenario() -> int:
    """Student 2201 owns INV-1, INV-2 and INV-3 (unpaid); student 2202 owns nothing."""
    owner_id = await make_student("2201", "Budi Santoso")
    await make_student("2202", "Siti Aminah", phone="+6285711112222")
    for code in ("INV-1", "INV-2", "INV-3"):
        await make_invoice(owner_id, code, amount=1_000_000)
    return owner_id


async def test_row_with_an_invoice_code_of_another_student_is_a_row_error(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    owner_id = await seed_owner_scenario()
    before = await invoice_by_code("INV-1")

    batch = await preview(
        client,
        staf_headers,
        # NIM typo on a paid row: must not mark student 2201's invoice paid.
        line(nim="2202", name="Siti Baru", phone="0812-9999-0000", code="INV-1", status="lunas"),
        line(nim="2201", name="", code="INV-2", amount="2000000"),  # matching owner: applies
        line(nim="2299", name="Stray", code="INV-3", status="lunas"),  # unknown NIM, same problem
    )

    assert batch["summary"] == {
        "rows_total": 3,
        "rows_valid": 1,
        "rows_error": 2,
        "students_new": 0,
        "students_updated": 1,
        "invoices_new": 0,
        "invoices_updated": 1,
        "invoices_skipped": 0,
    }
    errors = (await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=staf_headers)).json()
    assert errors["items"] == [
        {
            "row_number": 2,
            "field": "invoice_code",
            "message": "Kode tagihan sudah terdaftar untuk NIM lain.",
        },
        {
            "row_number": 4,
            "field": "invoice_code",
            "message": "Kode tagihan sudah terdaftar untuk NIM lain.",
        },
    ]

    applied = await apply(client, staf_headers, batch["id"])

    assert applied.status_code == 200
    assert applied.json()["summary"]["invoices_updated"] == 1
    after = await invoice_by_code("INV-1")
    assert (after.amount, after.status, after.paid_at, after.student_id) == (
        before.amount,
        "unpaid",
        None,
        owner_id,
    )
    assert after.updated_at == before.updated_at
    assert (await invoice_by_code("INV-3")).status == "unpaid"
    assert (await invoice_by_code("INV-2")).amount == 2_000_000  # matching row still applied
    other = await student_by_nim("2202")
    assert other.name == "Siti Aminah"  # the mismatching row did not touch its student
    assert other.phone == "+6285711112222"
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).where(Student.nim == "2299")) == 0


async def test_soft_deleted_invoice_of_another_student_is_also_a_row_error(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await seed_owner_scenario()
    async with get_session_factory()() as db:
        invoice = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-1"))
        assert invoice is not None
        invoice.deleted_at = datetime(2026, 10, 1, tzinfo=UTC)
        await db.commit()

    batch = await preview(client, staf_headers, line(nim="2202", name="", code="INV-1"))

    assert batch["summary"]["rows_valid"] == 0
    assert batch["summary"]["rows_error"] == 1


async def test_apply_rechecks_the_invoice_owner_when_the_database_changed_after_preview(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    # At preview time neither INV-1 nor student 2201 exists, so the row is accepted.
    batch = await preview(
        client,
        staf_headers,
        line(nim="2201", name="Budi", code="INV-1", status="lunas"),
        line(nim="2202", name="Siti", code="INV-2"),
    )
    assert batch["summary"]["rows_valid"] == 2
    other_id = await make_student("2209", "Rudi")
    await make_invoice(other_id, "INV-1", amount=1_000_000)  # another import created it meanwhile

    applied = await apply(client, staf_headers, batch["id"])

    assert applied.status_code == 200
    summary = applied.json()["summary"]
    assert summary["students_new"] == 1  # only 2202; the skipped row created no student
    assert summary["students_updated"] == 0
    assert summary["invoices_new"] == 1
    assert summary["invoices_updated"] == 0
    assert summary["invoices_skipped"] == 0
    invoice = await invoice_by_code("INV-1")
    assert (invoice.student_id, invoice.status, invoice.amount) == (other_id, "unpaid", 1_000_000)
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).where(Student.nim == "2201")) == 0


# --- phone status transitions when the file has no phone ---------------------------------------


async def test_no_phone_in_file_keeps_invalid_status_and_otherwise_marks_missing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2201", "Budi", phone=None, phone_status="invalid")
    await make_student("2202", "Siti", phone=None, phone_status="valid")  # no number behind it

    await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="", code="INV-1", phone=""),
        line(nim="2202", name="", code="INV-2", phone=""),
    )

    assert (
        await student_by_nim("2201")
    ).phone_status == "invalid"  # known-bad number stays flagged
    assert (await student_by_nim("2202")).phone_status == "missing"


# --- apply guards ------------------------------------------------------------------------------


async def test_apply_without_valid_rows_is_422_and_leaves_the_batch_in_preview(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line(amount="abc"))
    assert batch["summary"]["rows_valid"] == 0

    response = await apply(client, staf_headers, batch["id"])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "Tidak ada baris valid untuk diterapkan."
    after = (await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=staf_headers)).json()
    assert after["status"] == "preview"
    assert after["applied_at"] is None
    assert await audit_events("import.applied") == []
    discarded = await client.post(f"{IMPORTS_URL}/{batch['id']}/discard", headers=staf_headers)
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "discarded"


async def test_same_nim_in_several_rows_creates_the_student_once(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    applied = await import_lines(
        client,
        staf_headers,
        line(nim="2201", name="Budi", prodi="TI", cohort="2020", code="INV-1", phone=""),
        line(nim="2201", name="Budi S.", prodi="", cohort="2021", code="INV-2", phone=""),
        line(nim="2201", name="", prodi="", cohort="", code="INV-3", phone="0857-1111-2222"),
    )

    assert applied["summary"]["students_new"] == 1
    assert applied["summary"]["students_updated"] == 0
    assert applied["summary"]["invoices_new"] == 3
    assert await count(Student) == 1
    student = await student_by_nim("2201")
    assert student.name == "Budi S."  # the last non-empty value wins
    assert student.cohort == "2021"
    assert student.study_program == "TI"  # empty cells never blank a value
    assert student.phone == "+6285711112222"
    assert student.phone_status == "valid"
    for code in ("INV-1", "INV-2", "INV-3"):
        assert (await invoice_by_code(code)).student_id == student.id
