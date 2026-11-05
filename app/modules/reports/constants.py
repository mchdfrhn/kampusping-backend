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

# Same wording as the message log in the web app, in the order a reader expects.
MESSAGE_STATUS_LABELS = {
    MessageStatus.QUEUED.value: "Antre",
    MessageStatus.SENDING.value: "Mengirim",
    MessageStatus.SENT.value: "Terkirim",
    MessageStatus.DELIVERED.value: "Diterima",
    MessageStatus.READ.value: "Dibaca",
    MessageStatus.FAILED.value: "Gagal",
    MessageStatus.CANCELLED.value: "Dibatalkan",
}
