"""Apply: upsert students and invoices from validated rows. The caller owns the transaction."""

from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.students.constants import PhoneStatus
from app.modules.students.model import Student

from .existing import invoices_by_code, is_sticky_skip, students_by_nim
from .validation import ValidRow


def _set(target: object, field: str, value: object) -> bool:
    """Assign when different; report whether anything changed."""
    if getattr(target, field) == value:
        return False
    setattr(target, field, value)
    return True


def _touch(target: Student | Invoice, actor: str, now: datetime) -> None:
    target.updated_by = actor
    target.updated_at = now


def _restore(target: Student | Invoice) -> bool:
    if target.deleted_at is None:
        return False
    target.deleted_at = None
    target.deleted_by = None
    return True


def _apply_phone(student: Student, row: ValidRow) -> bool:
    """Phone rules: a usable number wins; an unusable one never replaces a valid number."""
    if row.phone:
        changed = _set(student, "phone", row.phone)
        return _set(student, "phone_status", PhoneStatus.VALID.value) or changed
    if row.phone_raw:  # something was typed but it is not a usable number
        has_valid = bool(student.phone) and student.phone_status == PhoneStatus.VALID.value
        return False if has_valid else _set(student, "phone_status", PhoneStatus.INVALID.value)
    if student.phone is None and student.phone_status != PhoneStatus.INVALID.value:
        return _set(student, "phone_status", PhoneStatus.MISSING.value)
    return False


def _upsert_student(
    student: Student | None, row: ValidRow, actor: str, now: datetime
) -> tuple[Student, bool]:
    """Create or update from one row; returns the student and whether it is new.

    ``opted_out_at`` / ``opted_out_reason`` are never touched: only staff change consent.
    """
    if student is None:
        if not row.name:  # preview rejects this; only a changed database can get here
            raise AppException(
                f"Nama wajib diisi untuk mahasiswa baru (NIM {row.nim}).",
                status_code=422,
                code=VALIDATION_ERROR,
            )
        student = Student(
            nim=row.nim,
            name=row.name,
            study_program=row.study_program,
            cohort=row.cohort,
            phone=None,
            phone_status=PhoneStatus.MISSING.value,
            created_by=actor,
            created_at=now,
            updated_by=actor,
            updated_at=now,
        )
        _apply_phone(student, row)
        return student, True

    changed = _restore(student)
    for field in ("name", "study_program", "cohort"):
        if value := getattr(row, field):  # an empty cell never blanks a stored value
            changed = _set(student, field, value) or changed
    changed = _apply_phone(student, row) or changed
    if changed:
        _touch(student, actor, now)
    return student, False


def _mark_paid(invoice: Invoice, row: ValidRow, source: str, today: date) -> None:
    invoice.status = InvoiceStatus.PAID.value
    invoice.paid_at = row.paid_at or today
    invoice.paid_via = row.paid_via or source


async def apply_rows(
    db: AsyncSession,
    rows: list[ValidRow],
    *,
    source: str,
    actor: str,
    now: datetime,
    today: date,
) -> dict[str, int]:
    """Upsert students by NIM, then invoices by code; returns the actual counts.

    An invoice that is already paid keeps its status, ``paid_at`` and ``paid_via``; an unpaid row
    against it only updates the other fields and counts as skipped. Invoices absent from the file
    are not touched.
    """
    students = await students_by_nim(db, [row.nim for row in rows])
    invoices = await invoices_by_code(db, [row.invoice_code for row in rows])

    new_nims: set[str] = set()
    updated_nims: set[str] = set()
    for row in rows:
        student, is_new = _upsert_student(students.get(row.nim), row, actor, now)
        if row.nim not in students:
            db.add(student)
            students[row.nim] = student
        if is_new:
            new_nims.add(row.nim)
        elif row.nim not in new_nims:
            updated_nims.add(row.nim)
    await db.flush()  # new students need their ids for the invoices

    counts = {"invoices_new": 0, "invoices_updated": 0, "invoices_skipped": 0}
    for row in rows:
        invoice = invoices.get(row.invoice_code)
        if invoice is None:
            invoice = Invoice(
                invoice_code=row.invoice_code,
                student_id=students[row.nim].id,
                semester=row.semester,
                amount=row.amount,
                due_date=row.due_date,
                status=InvoiceStatus.UNPAID.value,
                source=source,
                created_by=actor,
                created_at=now,
                updated_by=actor,
                updated_at=now,
            )
            if row.status == InvoiceStatus.PAID.value:
                _mark_paid(invoice, row, source, today)
            db.add(invoice)
            invoices[row.invoice_code] = invoice
            counts["invoices_new"] += 1
            continue

        skipped = is_sticky_skip(invoice, row)
        changed = _restore(invoice)
        changed = _set(invoice, "amount", row.amount) or changed
        changed = _set(invoice, "due_date", row.due_date) or changed
        changed = _set(invoice, "semester", row.semester) or changed
        changed = _set(invoice, "source", source) or changed
        if row.status == InvoiceStatus.PAID.value and invoice.status != InvoiceStatus.PAID.value:
            _mark_paid(invoice, row, source, today)
            changed = True
        if changed:
            _touch(invoice, actor, now)
        counts["invoices_skipped" if skipped else "invoices_updated"] += 1
    await db.flush()

    return {
        "students_new": len(new_nims),
        "students_updated": len(updated_nims),
        **counts,
    }
