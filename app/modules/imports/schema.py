from datetime import datetime

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


class ImportRowErrorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    field: str
    message: str
