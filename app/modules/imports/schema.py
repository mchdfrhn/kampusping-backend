from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ImportBatchRead(BaseModel):
    """A batch without its stored rows payload."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    file_name: str
    semester: str | None
    status: str
    summary: dict[str, int]
    created_at: datetime
    created_by: str | None
    applied_at: datetime | None
    has_change_log: bool
    rolled_back_at: datetime | None
    # Why an applied batch cannot be rolled back now; ``None`` when it can (or is not applied).
    # Filled by ``ImportService.read_models`` on the list and detail endpoints only.
    rollback_block: Literal["no_change_log", "not_latest"] | None = None


class ImportRowErrorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    field: str
    message: str


class RollbackConflictRead(BaseModel):
    """A row changed since the import; field names only, never values (personal data)."""

    entity: str
    label: str
    fields: list[str]


class RollbackPreview(BaseModel):
    students_deleted: int
    invoices_deleted: int
    students_reverted: int
    invoices_reverted: int
    queued_messages_cancelled: int
    sent_messages: int
    conflicts: list[RollbackConflictRead]
    conflicts_total: int
