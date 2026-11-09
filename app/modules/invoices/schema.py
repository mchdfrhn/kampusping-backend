from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.modules.students.schema import StudentSummary

from .constants import InvoiceStatus


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_code: str
    student: StudentSummary
    semester: str
    amount: int
    due_date: date
    status: str
    paid_at: date | None
    paid_via: str | None
    source: str
    exempt_reason: str | None
    exempt_until: date | None
    escalated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvoiceStatusUpdate(BaseModel):
    status: InvoiceStatus


class InvoiceDeletePreviewItem(BaseModel):
    id: int
    invoice_code: str
    student_nim: str
    student_name: str
    semester: str
    amount: int
    due_date: date
    status: str


class InvoiceDeleteByFilePreview(BaseModel):
    total_codes_in_file: int
    found_count: int
    not_found_count: int
    affected_messages_count: int
    found_invoices: list[InvoiceDeletePreviewItem]
    not_found_codes: list[str]


class InvoiceDeleteByCodesRequest(BaseModel):
    invoice_codes: list[str]
    hard_delete: bool = False


class InvoiceDeleteByFileResult(BaseModel):
    deleted_invoices_count: int
    affected_messages_count: int
    not_found_count: int
