"""Sample ``ReportData`` for the Excel and PDF builder tests."""

from datetime import UTC, date, datetime
from typing import Any

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

GENERATED_AT = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)  # 10:00 in Jakarta
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
