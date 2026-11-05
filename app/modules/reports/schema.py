from datetime import date, datetime

from pydantic import BaseModel

from .constants import ReportPreset


class InvoiceFigures(BaseModel):
    """Counts and rupiah sums. ``overdue`` and ``exempt`` are disjoint subsets of ``unpaid``."""

    total: int
    total_amount: int
    paid: int
    paid_amount: int
    unpaid: int
    unpaid_amount: int
    overdue: int
    overdue_amount: int
    exempt: int
    exempt_amount: int
    paid_percent_count: float
    paid_percent_amount: float


class MessageFigures(BaseModel):
    queued: int
    sending: int
    sent: int
    delivered: int
    read: int
    failed: int
    cancelled: int


class PaymentsInPeriod(BaseModel):
    count: int
    amount: int


class ReportSummary(BaseModel):
    invoices: InvoiceFigures
    messages: MessageFigures
    paid_after_message: int
    # Only the weekly preset has a payment window.
    payments_in_period: PaymentsInPeriod | None


class GroupRow(BaseModel):
    study_program: str
    cohort: str
    total: int
    paid: int
    unpaid: int
    overdue: int
    overdue_amount: int
    paid_percent: float


class ArrearsRow(BaseModel):
    """One overdue invoice. Deliberately has no phone number: reports never carry one."""

    nim: str
    name: str
    study_program: str
    cohort: str
    semester: str
    invoice_code: str
    amount: int
    due_date: date
    days_overdue: int
    messages_sent: int
    last_message_date: date | None
    last_message_status: str | None


class ReportData(BaseModel):
    preset: ReportPreset
    semester: str | None
    period_start: date | None
    period_end: date | None
    generated_at: datetime
    generated_by: str
    summary: ReportSummary
    groups: list[GroupRow]
    arrears: list[ArrearsRow]


class ReportPreview(BaseModel):
    """What the screen shows: ``ReportData`` without the arrears rows, only their count."""

    preset: ReportPreset
    semester: str | None
    period_start: date | None
    period_end: date | None
    generated_at: datetime
    generated_by: str
    summary: ReportSummary
    groups: list[GroupRow]
    arrears_count: int


class ReportOptions(BaseModel):
    semesters: list[str]
