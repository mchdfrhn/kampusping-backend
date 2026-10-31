from datetime import datetime

from pydantic import BaseModel


class InvoiceSummary(BaseModel):
    """Counts and rupiah sums; ``overdue`` is a subset of ``unpaid``."""

    total: int
    total_amount: int
    paid: int
    paid_amount: int
    unpaid: int
    unpaid_amount: int
    overdue: int
    overdue_amount: int


class MessageSummary(BaseModel):
    queued: int
    sending: int
    sent: int
    delivered: int
    read: int
    failed: int
    cancelled: int


class WarningsSummary(BaseModel):
    paused_reason: str | None
    auto_send_enabled: bool


class DashboardSummary(BaseModel):
    """Aggregates only: no student, invoice or message detail ever appears here."""

    invoices: InvoiceSummary
    messages: MessageSummary
    warnings: WarningsSummary
    semester: str | None
    generated_at: datetime
