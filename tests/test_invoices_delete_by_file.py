"""Tests for deleting invoices by uploading a file or passing codes."""

import io
from datetime import date

import httpx
import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import STAF_KEUANGAN_ROLE
from app.db.session import get_session_factory
from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.file_parser import extract_invoice_codes_from_file
from app.modules.invoices.model import Invoice
from app.modules.messages.constants import MessageStatus
from app.modules.messages.model import Message
from app.modules.students.model import Student

from .helpers import auth_headers, create_user


def make_xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_extract_invoice_codes_csv() -> None:
    content = b"kode tagihan,nama\nINV-001,Budi\nINV-002,Ani\n"
    codes = extract_invoice_codes_from_file("test.csv", content)
    assert codes == ["INV-001", "INV-002"]


def test_extract_invoice_codes_single_column_csv() -> None:
    content = b"INV-101\nINV-102\nINV-103\n"
    codes = extract_invoice_codes_from_file("codes.csv", content)
    assert codes == ["INV-101", "INV-102", "INV-103"]


def test_extract_invoice_codes_xlsx() -> None:
    content = make_xlsx([["NIM", "Kode Tagihan", "Nominal"], ["2021001", "INV-999", 500000]])
    codes = extract_invoice_codes_from_file("test.xlsx", content)
    assert codes == ["INV-999"]


def test_extract_invoice_codes_missing_column() -> None:
    content = b"nama,prodi\nBudi,Informatika\n"
    with pytest.raises(Exception, match="Kolom kode tagihan tidak ditemukan"):
        extract_invoice_codes_from_file("test.csv", content)


async def _seed_student_and_invoices(db: AsyncSession) -> tuple[Student, list[Invoice]]:
    student = Student(
        nim="123456",
        name="Test Mahasiswa",
        study_program="Teknik Informatika",
        created_by="test",
        updated_by="test",
    )
    db.add(student)
    await db.flush()

    inv1 = Invoice(
        invoice_code="INV-A1",
        student_id=student.id,
        semester="2024/2025 Ganjil",
        amount=1000000,
        due_date=date(2024, 12, 1),
        status=InvoiceStatus.UNPAID.value,
        source="siakad",
        created_by="test",
        updated_by="test",
    )
    inv2 = Invoice(
        invoice_code="INV-A2",
        student_id=student.id,
        semester="2024/2025 Ganjil",
        amount=1500000,
        due_date=date(2024, 12, 1),
        status=InvoiceStatus.UNPAID.value,
        source="siakad",
        created_by="test",
        updated_by="test",
    )
    db.add_all([inv1, inv2])
    await db.flush()

    msg = Message(
        student_id=student.id,
        invoice_id=inv1.id,
        kind="auto",
        scheduled_date=date(2024, 11, 24),
        body="Tagihan INV-A1",
        status=MessageStatus.QUEUED.value,
        created_by="test",
        updated_by="test",
    )
    db.add(msg)
    await db.commit()
    return student, [inv1, inv2]


@pytest.mark.asyncio
async def test_preview_delete_by_file(client: httpx.AsyncClient) -> None:
    async with get_session_factory()() as db:
        await _seed_student_and_invoices(db)

    await create_user("staf@example.com", STAF_KEUANGAN_ROLE)
    headers = await auth_headers(client, "staf@example.com")

    file_content = b"Kode Tagihan\nINV-A1\nINV-NOTFOUND\n"
    response = await client.post(
        "/api/v1/invoices/delete-by-file/preview",
        headers=headers,
        files={"file": ("tagihan.csv", file_content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_codes_in_file"] == 2
    assert data["found_count"] == 1
    assert data["not_found_count"] == 1
    assert data["affected_messages_count"] == 1
    assert data["found_invoices"][0]["invoice_code"] == "INV-A1"
    assert data["not_found_codes"] == ["INV-NOTFOUND"]


@pytest.mark.asyncio
async def test_soft_delete_by_file(client: httpx.AsyncClient) -> None:
    async with get_session_factory()() as db:
        await _seed_student_and_invoices(db)

    await create_user("staf_soft@example.com", STAF_KEUANGAN_ROLE)
    headers = await auth_headers(client, "staf_soft@example.com")

    file_content = b"Kode Tagihan\nINV-A1\n"
    response = await client.post(
        "/api/v1/invoices/delete-by-file",
        headers=headers,
        files={"file": ("tagihan.csv", file_content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["deleted_invoices_count"] == 1
    assert data["affected_messages_count"] == 1

    # Verify soft deleted in DB
    async with get_session_factory()() as db:
        inv = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-A1"))
        assert inv is not None
        assert inv.deleted_at is not None

        # Queued message cancelled
        msg = await db.scalar(select(Message).where(Message.invoice_id == inv.id))
        assert msg is not None
        assert msg.status == MessageStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_hard_delete_by_codes(client: httpx.AsyncClient) -> None:
    async with get_session_factory()() as db:
        await _seed_student_and_invoices(db)

    await create_user("staf_hard@example.com", STAF_KEUANGAN_ROLE)
    headers = await auth_headers(client, "staf_hard@example.com")

    response = await client.post(
        "/api/v1/invoices/delete-by-codes",
        headers=headers,
        json={"invoice_codes": ["INV-A1", "INV-A2"], "hard_delete": True},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["deleted_invoices_count"] == 2

    # Verify hard deleted in DB
    async with get_session_factory()() as db:
        inv = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-A1"))
        assert inv is None
        inv2 = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-A2"))
        assert inv2 is None
