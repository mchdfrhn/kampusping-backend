"""Sample ``ReportData`` for the Excel and PDF builder tests."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.reports.constants import ReportPreset
from app.modules.reports.schema import (
    ArrearsRow,
    GroupRow,
    InvoiceFigures,
    MessageFigures,
    PaymentsInPeriod,
    ReportData,
    ReportSummary,
)
from app.modules.students.model import Student

NOW = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)  # 10:00 WIB, so "today" is 2026-11-02
TODAY = date(2026, 11, 2)
GENERATED_AT = NOW
SEM_A = "Ganjil 2026/2027"
GENERATED_BY = "staf@example.com"


def make_arrears_row(**overrides: Any) -> ArrearsRow:
    values: dict[str, Any] = {
        "nim": "2201001",
        "name": "Budi Santoso",
        "study_program": "Teknik Informatika",
        "cohort": "2022",
        "semester": "Ganjil 2026/2027",
        "invoice_code": "INV-001",
        "amount": 1_500_000,
        "due_date": date(2026, 10, 20),
        "days_overdue": 13,
        "messages_sent": 2,
        "last_message_date": date(2026, 10, 25),
        "last_message_status": "delivered",
    }
    values.update(overrides)
    return ArrearsRow(**values)


def make_group(**overrides: Any) -> GroupRow:
    values: dict[str, Any] = {
        "study_program": "Teknik Informatika",
        "cohort": "2022",
        "total": 4,
        "paid": 1,
        "unpaid": 3,
        "overdue": 2,
        "overdue_amount": 3_000_000,
        "paid_percent": 25.0,
    }
    values.update(overrides)
    return GroupRow(**values)


def make_report(
    *,
    preset: ReportPreset = ReportPreset.WEEKLY,
    semester: str | None = None,
    groups: list[GroupRow] | None = None,
    arrears: list[ArrearsRow] | None = None,
    empty: bool = False,
) -> ReportData:
    weekly = preset is ReportPreset.WEEKLY
    zero = 0 if empty else None

    def figure(value: int) -> int:
        return value if zero is None else zero

    summary = ReportSummary(
        invoices=InvoiceFigures(
            total=figure(10),
            total_amount=figure(10_000_000),
            paid=figure(4),
            paid_amount=figure(4_000_000),
            unpaid=figure(6),
            unpaid_amount=figure(6_000_000),
            overdue=figure(3),
            overdue_amount=figure(3_000_000),
            exempt=figure(1),
            exempt_amount=figure(1_000_000),
            paid_percent_count=0.0 if empty else 40.0,
            paid_percent_amount=0.0 if empty else 40.0,
        ),
        messages=MessageFigures(
            queued=figure(2),
            sending=0,
            sent=figure(5),
            delivered=figure(3),
            read=figure(2),
            failed=figure(1),
            cancelled=0,
        ),
        paid_after_message=figure(3),
        payments_in_period=PaymentsInPeriod(count=figure(2), amount=figure(1_500_000))
        if weekly
        else None,
    )
    if empty:
        groups, arrears = [], []
    return ReportData(
        preset=preset,
        semester=None if weekly else semester,
        period_start=date(2026, 10, 27) if weekly else None,
        period_end=date(2026, 11, 2) if weekly else None,
        generated_at=GENERATED_AT,
        generated_by=GENERATED_BY,
        summary=summary,
        groups=[make_group()] if groups is None else groups,
        arrears=[make_arrears_row()] if arrears is None else arrears,
    )


async def add_student(
    nim: str,
    *,
    name: str = "Budi Santoso",
    program: str | None = "Teknik Informatika",
    cohort: str | None = "2022",
    deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        student = Student(
            nim=nim,
            name=name,
            study_program=program,
            cohort=cohort,
            phone="+6281234567890",
            phone_status="valid",
            deleted_at=NOW if deleted else None,
        )
        db.add(student)
        await db.commit()
        return student.id


async def add_invoice(
    student_id: int,
    code: str,
    *,
    semester: str = SEM_A,
    amount: int = 1_000_000,
    due: date = TODAY + timedelta(days=5),
    status: str = "unpaid",
    paid_at: date | None = None,
    exempt_reason: str | None = None,
    exempt_until: date | None = None,
    deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        invoice = Invoice(
            invoice_code=code,
            student_id=student_id,
            semester=semester,
            amount=amount,
            due_date=due,
            status=status,
            paid_at=paid_at,
            source="siakad",
            exempt_reason=exempt_reason,
            exempt_until=exempt_until,
            deleted_at=NOW if deleted else None,
        )
        db.add(invoice)
        await db.commit()
        return invoice.id


async def add_message(
    student_id: int,
    invoice_id: int,
    *,
    status: str = "sent",
    scheduled: date = TODAY,
    sent_at: datetime | None = None,
    deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        message = Message(
            student_id=student_id,
            invoice_id=invoice_id,
            kind="manual",
            scheduled_date=scheduled,
            body="Pesan uji",
            status=status,
            queued_at=NOW,
            sent_at=sent_at,
            deleted_at=NOW if deleted else None,
        )
        db.add(message)
        await db.commit()
        return message.id


def at(year: int, month: int, day: int, hour: int = 5) -> datetime:
    """A UTC instant; 05:00 UTC is 12:00 in Jakarta, the same calendar day."""
    return datetime(year, month, day, hour, 0, tzinfo=UTC)
