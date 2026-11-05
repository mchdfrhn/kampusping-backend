from enum import StrEnum

from app.modules.messages.constants import MessageStatus


class ReportPreset(StrEnum):
    WEEKLY = "weekly"
    SEMESTER = "semester"


class ReportFormat(StrEnum):
    XLSX = "xlsx"
    PDF = "pdf"


# The weekly window is `[today - 6, today]`, both ends included.
WEEKLY_WINDOW_DAYS = 7

NO_PROGRAM_LABEL = "(tanpa prodi)"
NO_COHORT_LABEL = "(tanpa angkatan)"

AUDIT_REPORT_EXPORTED = "report.exported"
AUDIT_ENTITY_REPORT = "report"

# Messages that actually reached (or are confirmed by) WhatsApp; `sending` has no `sent_at` yet.
REACHED_STATUSES = (
    MessageStatus.SENT.value,
    MessageStatus.DELIVERED.value,
    MessageStatus.READ.value,
)
