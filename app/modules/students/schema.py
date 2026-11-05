from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .constants import PHONE_INPUT_MAX_LENGTH


class StudentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nim: str
    name: str
    study_program: str | None
    cohort: str | None
    phone: str | None
    phone_status: str
    opted_out_at: datetime | None
    opted_out_reason: str | None
    created_at: datetime
    updated_at: datetime


class StudentSummary(BaseModel):
    """Compact student reference embedded in other resources."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nim: str
    name: str


class StudentFilterOptions(BaseModel):
    """Values the student list can be filtered by, taken from the stored students."""

    study_programs: list[str]
    cohorts: list[str]


class StudentPhoneUpdate(BaseModel):
    phone: str | None = Field(
        max_length=PHONE_INPUT_MAX_LENGTH,
        description="Any common Indonesian format; null or empty clears the number.",
    )
