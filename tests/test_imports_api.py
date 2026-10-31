import asyncio
import io
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import AppException
from app.db.base import Base
from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch, ImportRowError
from app.modules.imports.service import ImportService
from app.modules.invoices.model import Invoice
from app.modules.students.model import Student
from app.modules.users.model import User

from .test_students_invoices import audit_events, freeze_clock, make_invoice, make_student

IMPORTS_URL = "/api/v1/imports"
HEADER = (
    "NIM,Nama,Prodi,Angkatan,No WA,Kode Tagihan,Nominal,Jatuh Tempo,Status,"
    "Tanggal Bayar,Kanal Pembayaran"
)
SEMESTER = "2025/2026 Ganjil"
SUMMARY_KEYS = {
    "rows_total",
    "rows_valid",
    "rows_error",
    "students_new",
    "students_updated",
    "invoices_new",
    "invoices_updated",
    "invoices_skipped",
}


def line(
    nim: str = "2201",
    name: str = "Budi Santoso",
    prodi: str = "Teknik Informatika",
    cohort: str = "2022",
    phone: str = "081234567890",
    code: str = "INV-1",
    amount: str = "5000000",
    due: str = "2026-11-15",
    status: str = "belum lunas",
    paid_at: str = "",
    via: str = "",
) -> str:
    return ",".join([nim, name, prodi, cohort, phone, code, amount, due, status, paid_at, via])


def make_csv(*lines: str) -> bytes:
    return "\n".join([HEADER, *lines]).encode("utf-8")


def make_xlsx(rows: Sequence[Sequence[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    content: bytes,
    *,
    source: str = "siakad",
    semester: str | None = SEMESTER,
    filename: str = "tagihan.csv",
) -> httpx.Response:
    data = {"source": source}
    if semester is not None:
        data["semester"] = semester
    return await client.post(
        IMPORTS_URL, files={"file": (filename, content)}, data=data, headers=headers
    )


async def preview(
    client: httpx.AsyncClient, headers: dict[str, str], *lines: str, **kwargs: Any
) -> dict[str, Any]:
    response = await upload(client, headers, make_csv(*lines), **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


async def apply(
    client: httpx.AsyncClient, headers: dict[str, str], batch_id: int
) -> httpx.Response:
    return await client.post(f"{IMPORTS_URL}/{batch_id}/apply", headers=headers)


async def import_lines(
    client: httpx.AsyncClient, headers: dict[str, str], *lines: str, **kwargs: Any
) -> dict[str, Any]:
    """Preview then apply; returns the applied batch."""
    batch = await preview(client, headers, *lines, **kwargs)
    response = await apply(client, headers, batch["id"])
    assert response.status_code == 200, response.text
    return response.json()


async def student_by_nim(nim: str) -> Student:
    async with get_session_factory()() as db:
        student = await db.scalar(select(Student).where(Student.nim == nim))
        assert student is not None
        return student


async def invoice_by_code(code: str) -> Invoice:
    async with get_session_factory()() as db:
        invoice = await db.scalar(select(Invoice).where(Invoice.invoice_code == code))
        assert invoice is not None
        return invoice


async def stored_rows(batch_id: int) -> list[dict[str, Any]] | None:
    """The batch's ``rows`` payload (a deferred column, so read it explicitly)."""
    async with get_session_factory()() as db:
        return await db.scalar(select(ImportBatch.rows).where(ImportBatch.id == batch_id))


async def rows_are_sql_null(batch_id: int) -> bool:
    """True for SQL NULL, not for a stored JSON ``null`` document."""
    async with get_session_factory()() as db:
        found = await db.scalar(
            select(func.count()).where(ImportBatch.id == batch_id, ImportBatch.rows.is_(None))
        )
        return found == 1


async def count(model: type[Student] | type[Invoice] | type[ImportBatch]) -> int:
    async with get_session_factory()() as db:
        return await db.scalar(select(func.count()).select_from(model)) or 0


# --- preview ----------------------------------------------------------------------------------


async def test_preview_changes_no_business_data(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2201", "Nama Lama", phone="+6281111111111")
    await make_invoice(1, "INV-1", amount=1_000_000)

    body = await preview(
        client,
        staf_headers,
        line(nim="2201", name="Nama Baru", code="INV-1", amount="2500000"),
        line(nim="2202", name="Siti Aminah", code="INV-2"),
        line(nim="2203", name="Rudi", code="INV-3", amount="abc"),
    )

    assert body["status"] == "preview"
    assert body["source"] == "siakad"
    assert body["file_name"] == "tagihan.csv"
    assert body["semester"] == SEMESTER
    assert body["applied_at"] is None
    assert body["created_by"] == "staf@example.com"
    assert "rows" not in body
    assert body["summary"] == {
        "rows_total": 3,
        "rows_valid": 2,
        "rows_error": 1,
        "students_new": 1,
        "students_updated": 1,
        "invoices_new": 1,
        "invoices_updated": 1,
        "invoices_skipped": 0,
    }
    # Nothing in the business tables moved.
    assert await count(Student) == 1
    assert await count(Invoice) == 1
    assert (await student_by_nim("2201")).name == "Nama Lama"
    assert (await invoice_by_code("INV-1")).amount == 1_000_000

    errors = await client.get(f"{IMPORTS_URL}/{body['id']}/errors", headers=staf_headers)
    assert errors.status_code == 200
    assert errors.json()["total"] == 1
    assert errors.json()["items"] == [
        {"row_number": 4, "field": "amount", "message": "Nominal tidak valid."}
    ]
    assert len(await audit_events("import.previewed")) == 1


async def test_preview_prediction_counts_soft_deleted_rows_as_existing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    await make_invoice(student_id, "INV-1")
    async with get_session_factory()() as db:
        for model in (Student, Invoice):
            row = (await db.scalars(select(model))).one()
            row.deleted_at = datetime(2026, 10, 1, tzinfo=UTC)
        await db.commit()

    body = await preview(client, staf_headers, line(nim="2201", name="", code="INV-1"))

    assert body["summary"]["students_new"] == 0
    assert body["summary"]["students_updated"] == 1
    assert body["summary"]["invoices_new"] == 0
    assert body["summary"]["invoices_updated"] == 1
    assert body["summary"]["rows_error"] == 0


async def test_duplicate_invoice_code_in_file_is_a_row_error_for_the_second_row(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview(
        client,
        staf_headers,
        line(nim="2201", code="INV-1", amount="1000000"),
        line(nim="2202", name="Siti", code="INV-2"),
        line(nim="2203", name="Rudi", code="INV-1", amount="9000000"),
    )

    assert batch["summary"]["rows_total"] == 3
    assert batch["summary"]["rows_valid"] == 2
    assert batch["summary"]["rows_error"] == 1
    assert batch["summary"]["invoices_new"] == 2
    errors = (await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=staf_headers)).json()
    assert errors["items"] == [
        {
            "row_number": 4,
            "field": "invoice_code",
            "message": "Kode tagihan duplikat dalam berkas (baris 2).",
        }
    ]

    applied = (await apply(client, staf_headers, batch["id"])).json()
    assert applied["summary"]["invoices_new"] == 2
    assert (await invoice_by_code("INV-1")).amount == 1_000_000  # the first row wins
    assert await count(Student) == 2  # the duplicate row's student was not created


async def test_new_student_without_name_is_row_error(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await make_student("2200", "Sudah Ada")
    batch = await preview(
        client,
        staf_headers,
        line(nim="2201", name="", code="INV-1"),  # new, no name: error
        line(nim="2200", name="", code="INV-2"),  # existing student: fine without a name
        line(nim="2202", name="Siti", code="INV-3"),  # new, named
        line(nim="2202", name="", code="INV-4"),  # same student, named by the earlier row: fine
    )

    assert batch["summary"]["rows_error"] == 1
    assert batch["summary"]["rows_valid"] == 3
    assert batch["summary"]["students_new"] == 1
    assert batch["summary"]["students_updated"] == 1
    errors = (await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=staf_headers)).json()
    assert errors["items"] == [
        {
            "row_number": 2,
            "field": "name",
            "message": "Nama wajib diisi untuk mahasiswa baru.",
        }
    ]


async def test_preview_stores_at_most_500_errors_but_counts_all(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    lines = [line(nim=f"{3000 + i}", code=f"INV-{i}", amount="x") for i in range(520)]
    batch = await preview(client, staf_headers, *lines)

    assert batch["summary"]["rows_error"] == 520
    assert batch["summary"]["rows_valid"] == 0
    errors = (
        await client.get(
            f"{IMPORTS_URL}/{batch['id']}/errors", params={"page_size": 100}, headers=staf_headers
        )
    ).json()
    assert errors["total"] == 500
    assert [item["row_number"] for item in errors["items"]][:3] == [2, 3, 4]  # ascending default


async def test_preview_accepts_xlsx_and_takes_only_the_file_basename(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    content = make_xlsx(
        [
            HEADER.split(","),
            [
                "2201",
                "Budi",
                "TI",
                "2022",
                "081234567890",
                "INV-1",
                5000000,
                date(2026, 11, 15),
                "belum lunas",
                None,
                None,
            ],
        ]
    )

    response = await upload(
        client, staf_headers, content, filename="../../evil/dir/laporan.xlsx", source="sipema"
    )

    assert response.status_code == 201, response.text
    assert response.json()["file_name"] == "laporan.xlsx"
    assert response.json()["source"] == "sipema"
    assert response.json()["summary"]["rows_valid"] == 1


@pytest.mark.parametrize(
    ("content", "filename", "fragment"),
    [
        (b"a,b\n1,2\n", "tagihan.txt", "Format berkas tidak didukung"),
        (b"NIM,Nama\n1,x\n", "tagihan.csv", "Kolom wajib tidak ditemukan"),
        (b"   \n", "tagihan.csv", "Berkas kosong"),
        pytest.param(
            b"x" * (5 * 1024 * 1024 + 1),
            "tagihan.csv",
            "Ukuran berkas melebihi batas 5 MB",
            id="oversize",
        ),
    ],
)
async def test_unusable_file_is_422_with_indonesian_message(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    content: bytes,
    filename: str,
    fragment: str,
) -> None:
    response = await upload(client, staf_headers, content, filename=filename)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert fragment in response.json()["error"]["message"]
    assert await count(ImportBatch) == 0


async def test_unknown_source_is_422(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    response = await upload(client, staf_headers, make_csv(line()), source="excel")

    assert response.status_code == 422
    assert await count(ImportBatch) == 0


async def test_semester_comes_from_the_form_when_the_file_has_no_column(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    without = await preview(client, staf_headers, line(), semester=None)
    assert without["semester"] is None
    assert without["summary"]["rows_error"] == 1

    with_form = await import_lines(client, staf_headers, line(code="INV-2"))
    assert (await invoice_by_code("INV-2")).semester == SEMESTER
    assert with_form["semester"] == SEMESTER


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


async def test_unlisted_invoices_untouched(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2201", "Budi")
    await make_invoice(student_id, "INV-OLD", amount=1_234_000, status="unpaid")
    before = await invoice_by_code("INV-OLD")

    await import_lines(client, staf_headers, line(nim="2201", name="", code="INV-NEW"))

    after = await invoice_by_code("INV-OLD")
    assert after.amount == 1_234_000
    assert after.status == "unpaid"
    assert after.updated_at == before.updated_at
    assert after.deleted_at is None
    assert await count(Invoice) == 2


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


async def test_failed_apply_leaves_batch_in_preview_and_changes_nothing(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = await preview(client, staf_headers, line(code="INV-1"))

    async def failing_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit store down")

    monkeypatch.setattr("app.modules.imports.service.record_audit", failing_audit)
    with pytest.raises(RuntimeError):
        await apply(client, staf_headers, batch["id"])
    monkeypatch.undo()

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


# --- history ----------------------------------------------------------------------------------


async def test_list_and_get_history(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await preview(client, staf_headers, line(), filename="a.csv")
    second = await preview(client, staf_headers, line(code="INV-2"), filename="b.csv")
    await apply(client, staf_headers, second["id"])

    listing = await client.get(IMPORTS_URL, headers=staf_headers)

    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]  # newest first
    assert set(body["items"][0]) == {
        "id",
        "source",
        "file_name",
        "semester",
        "status",
        "summary",
        "created_at",
        "created_by",
        "applied_at",
    }
    assert set(body["items"][0]["summary"]) == SUMMARY_KEYS

    by_name = await client.get(
        IMPORTS_URL, params={"sort_by": "file_name", "sort_order": "asc"}, headers=staf_headers
    )
    assert [item["file_name"] for item in by_name.json()["items"]] == ["a.csv", "b.csv"]
    bad_sort = await client.get(IMPORTS_URL, params={"sort_by": "rows"}, headers=staf_headers)
    assert bad_sort.status_code == 422

    single = await client.get(f"{IMPORTS_URL}/{first['id']}", headers=staf_headers)
    assert single.status_code == 200
    assert single.json()["file_name"] == "a.csv"
    assert "rows" not in single.json()


# --- access -----------------------------------------------------------------------------------


async def test_imports_require_authentication(client: httpx.AsyncClient) -> None:
    for method, url in [
        ("GET", IMPORTS_URL),
        ("GET", f"{IMPORTS_URL}/1"),
        ("GET", f"{IMPORTS_URL}/1/errors"),
        ("POST", f"{IMPORTS_URL}/1/apply"),
        ("POST", f"{IMPORTS_URL}/1/discard"),
    ]:
        assert (await client.request(method, url)).status_code == 401, (method, url)
    response = await client.post(
        IMPORTS_URL, files={"file": ("t.csv", make_csv(line()))}, data={"source": "siakad"}
    )
    assert response.status_code == 401


async def test_pimpinan_cannot_use_imports(
    client: httpx.AsyncClient, pimpinan_headers: dict[str, str], staf_headers: dict[str, str]
) -> None:
    batch = await preview(client, staf_headers, line())

    assert (await upload(client, pimpinan_headers, make_csv(line()))).status_code == 403
    assert (await client.get(IMPORTS_URL, headers=pimpinan_headers)).status_code == 403
    assert (
        await client.get(f"{IMPORTS_URL}/{batch['id']}", headers=pimpinan_headers)
    ).status_code == 403
    assert (
        await client.get(f"{IMPORTS_URL}/{batch['id']}/errors", headers=pimpinan_headers)
    ).status_code == 403
    assert (await apply(client, pimpinan_headers, batch["id"])).status_code == 403
    assert (
        await client.post(f"{IMPORTS_URL}/{batch['id']}/discard", headers=pimpinan_headers)
    ).status_code == 403
    assert await count(Student) == 0


async def test_staf_and_admin_can_import_and_audit_events_are_recorded(
    client: httpx.AsyncClient, staf_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    staf_batch = await import_lines(client, staf_headers, line(code="INV-1"))
    admin_response = await upload(client, admin_headers, make_csv(line(code="INV-2")))

    assert admin_response.status_code == 201
    previewed = await audit_events("import.previewed")
    applied = await audit_events("import.applied")
    assert len(previewed) == 2
    assert len(applied) == 1
    assert applied[0].actor == "staf@example.com"
    assert applied[0].entity == "import_batch"
    assert applied[0].entity_id == str(staf_batch["id"])
    assert applied[0].detail is not None
    assert set(applied[0].detail) == SUMMARY_KEYS  # counts only, no row data
    assert await count(ImportBatch) == 2
    async with get_session_factory()() as db:
        assert await db.scalar(select(func.count()).select_from(ImportRowError)) == 0
