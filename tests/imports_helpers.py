"""Shared fixtures-free helpers for the import API tests."""

import io
from collections.abc import Sequence
from typing import Any

import httpx
from openpyxl import Workbook
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch
from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

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
