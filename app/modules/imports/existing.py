"""Lookups of rows an import may touch, and the rule about paid invoices.

Lookups deliberately include soft-deleted rows: ``nim`` and ``invoice_code`` are unique even
across deleted rows, and applying a file restores a deleted match instead of colliding with it.
"""

from collections.abc import Iterator, Sequence
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

from .constants import LOOKUP_CHUNK
from .validation import ValidRow

_T = TypeVar("_T")


def _chunks(values: Sequence[_T]) -> Iterator[Sequence[_T]]:
    for start in range(0, len(values), LOOKUP_CHUNK):
        yield values[start : start + LOOKUP_CHUNK]


async def students_by_nim(db: AsyncSession, nims: Sequence[str]) -> dict[str, Student]:
    found: dict[str, Student] = {}
    for chunk in _chunks(sorted(set(nims))):
        for student in await db.scalars(select(Student).where(Student.nim.in_(chunk))):
            found[student.nim] = student
    return found


async def invoices_by_code(db: AsyncSession, codes: Sequence[str]) -> dict[str, Invoice]:
    found: dict[str, Invoice] = {}
    for chunk in _chunks(sorted(set(codes))):
        stmt = (
            select(Invoice).where(Invoice.invoice_code.in_(chunk)).options(noload(Invoice.student))
        )
        for invoice in await db.scalars(stmt):
            found[invoice.invoice_code] = invoice
    return found


async def invoice_owner_nims(db: AsyncSession, invoices: dict[str, Invoice]) -> dict[str, str]:
    """``invoice_code`` -> NIM of the student the stored invoice belongs to (deleted ones too)."""
    student_ids = sorted({invoice.student_id for invoice in invoices.values()})
    nim_by_id: dict[int, str] = {}
    for chunk in _chunks(student_ids):
        result = await db.execute(select(Student.id, Student.nim).where(Student.id.in_(chunk)))
        nim_by_id.update(result.tuples().all())
    return {
        code: nim_by_id[invoice.student_id]
        for code, invoice in invoices.items()
        if invoice.student_id in nim_by_id
    }


def belongs_to_other_student(row: ValidRow, owners: dict[str, str]) -> bool:
    """The invoice code exists and is stored for a different NIM than the row's (likely a typo).

    Applying such a row would change, or mark paid, another student's invoice.
    """
    owner = owners.get(row.invoice_code)
    return owner is not None and owner != row.nim


def is_sticky_skip(invoice: Invoice, row: ValidRow) -> bool:
    """A file never turns a paid invoice back to unpaid (paid is sticky)."""
    return invoice.status == InvoiceStatus.PAID.value and row.status == InvoiceStatus.UNPAID.value
