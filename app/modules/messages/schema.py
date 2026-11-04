from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.modules.students.schema import StudentSummary
from app.shared.phone import mask_phone

from .model import Message


class MessageRead(BaseModel):
    """A row of the message log. The phone number is only ever exposed masked."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    student: StudentSummary
    phone_masked: str | None
    invoice_id: int
    rule_id: int | None
    kind: str
    scheduled_date: date
    body: str
    status: str
    cancel_reason: str | None
    attempts: int
    error: str | None
    queued_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    created_at: datetime

    @classmethod
    def from_message(cls, message: Message) -> "MessageRead":
        """Build from a message whose ``student`` is already loaded."""
        return cls(
            id=message.id,
            student=StudentSummary.model_validate(message.student),
            phone_masked=mask_phone(message.student.phone),
            invoice_id=message.invoice_id,
            rule_id=message.rule_id,
            kind=message.kind,
            scheduled_date=message.scheduled_date,
            body=message.body,
            status=message.status,
            cancel_reason=message.cancel_reason,
            attempts=message.attempts,
            error=message.error,
            queued_at=message.queued_at,
            sent_at=message.sent_at,
            delivered_at=message.delivered_at,
            read_at=message.read_at,
            created_at=message.created_at,
        )


ReceiptState = Literal["delivered", "read", "failed"]


class ManualRecipientItem(BaseModel):
    invoice_id: int
    invoice_code: str
    student_id: int
    student_name: str
    nim: str
    phone_masked: str | None
    study_program: str | None
    cohort: str | None
    semester: str
    amount: int
    due_date: date
    invoice_status: str
    is_eligible: bool
    exclude_reason: str | None


class ExcludedBreakdown(BaseModel):
    paid: int = 0
    opted_out: int = 0
    exempt: int = 0
    invalid_phone: int = 0
    already_queued: int = 0


class SamplePreview(BaseModel):
    student_name: str
    nim: str
    phone_masked: str | None
    invoice_code: str
    amount: int
    body: str


class ManualMessagePreviewRequest(BaseModel):
    template_id: int
    cohort: str | None = None
    study_program: str | None = None
    semester: str | None = None
    amount_min: int | None = None
    amount_max: int | None = None
    status: str | None = None
    search: str | None = None
    invoice_ids: list[int] | None = None


class ManualMessagePreviewRead(BaseModel):
    total_matched: int
    eligible_count: int
    excluded_count: int
    excluded_breakdown: ExcludedBreakdown
    sample_preview: SamplePreview | None
    items: list[ManualRecipientItem]


class ManualMessageSendRequest(BaseModel):
    template_id: int
    cohort: str | None = None
    study_program: str | None = None
    semester: str | None = None
    amount_min: int | None = None
    amount_max: int | None = None
    status: str | None = None
    search: str | None = None
    invoice_ids: list[int] | None = None


class ManualMessageSendRead(BaseModel):
    batch_ref: str
    queued_count: int
    excluded_count: int
    total_matched: int


class ManualFilterOptionsRead(BaseModel):
    cohorts: list[str]
    study_programs: list[str]
    semesters: list[str]
