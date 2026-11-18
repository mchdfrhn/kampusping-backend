"""Invoices that vanished from a rekap file: suspected paid, never marked paid on their own.

A "Rekap Tunggakan" lists only students who still owe. A student who paid simply disappears, but
so does one who left the active filter, so the preview shows these invoices and staff decide.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

from .constants import INCOMPLETE_EXPORT_THRESHOLD, REKAP_CODE_PREFIX
from .validation import ValidRow


@dataclass(frozen=True)
class SuspectedPaid:
    invoice_id: int
    invoice_code: str
    nim: str
    name: str
    study_program: str | None
    semester: str
    amount: int


@dataclass(frozen=True)
class SuspectedPaidResult:
    items: list[SuspectedPaid]
    # Open rekap invoices of the file's semesters (the invoices the file could have mentioned).
    pool_size: int

    @property
    def incomplete(self) -> bool:
        """So many vanished at once that the export itself is probably partial."""
        return self.pool_size > 0 and len(self.items) / self.pool_size > INCOMPLETE_EXPORT_THRESHOLD


async def find_suspected_paid(db: AsyncSession, rows: Sequence[ValidRow]) -> SuspectedPaidResult:
    """Open rekap invoices of the semesters present in ``rows`` whose code is not in ``rows``.

    A file without valid rows names no semester, so it suspects nothing.
    """
    semesters = sorted({row.semester for row in rows})
    if not semesters:
        return SuspectedPaidResult(items=[], pool_size=0)

    stmt = (
        select(
            Invoice.id,
            Invoice.invoice_code,
            Invoice.semester,
            Invoice.amount,
            Student.nim,
            Student.name,
            Student.study_program,
        )
        .join(Student, Student.id == Invoice.student_id)
        .where(
            Invoice.deleted_at.is_(None),
            Invoice.status == InvoiceStatus.UNPAID.value,
            Invoice.invoice_code.startswith(REKAP_CODE_PREFIX),
            Invoice.semester.in_(semesters),
        )
        .order_by(Student.nim, Invoice.invoice_code)
    )
    pool = (await db.execute(stmt)).all()
    in_file = {row.invoice_code for row in rows}
    items = [
        SuspectedPaid(
            invoice_id=found.id,
            invoice_code=found.invoice_code,
            nim=found.nim,
            name=found.name,
            study_program=found.study_program,
            semester=found.semester,
            amount=found.amount,
        )
        for found in pool
        if found.invoice_code not in in_file
    ]
    return SuspectedPaidResult(items=items, pool_size=len(pool))
