from datetime import datetime, time
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from .constants import MAX_ATTEMPTS_LIMIT

CampusName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
UrlText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


def parse_working_days(value: str) -> list[int]:
    return [int(part) for part in value.split(",") if part.strip()]


def format_working_days(days: list[int]) -> str:
    return ",".join(str(day) for day in sorted(days))


class SettingsWrite(BaseModel):
    """Editable settings. Internal state (pause reason, failure counter) is excluded."""

    campus_name: CampusName = ""
    finance_contact: ShortText = ""
    payment_link_siakad: UrlText = ""
    payment_guide_sipema: LongText = ""

    window_start: time
    window_end: time
    working_days: list[int]

    auto_send_enabled: bool
    daily_cap: int = Field(ge=1)
    throttle_min_s: int = Field(ge=0)
    throttle_max_s: int = Field(ge=0)
    max_attempts: int = Field(ge=1, le=MAX_ATTEMPTS_LIMIT)
    consecutive_failure_limit: int = Field(ge=1)
    escalation_offset_days: int = Field(ge=1)

    @field_validator("working_days")
    @classmethod
    def validate_working_days(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("Pilih minimal satu hari kerja.")
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("Hari kerja harus berupa angka 1 (Senin) sampai 7 (Minggu).")
        if len(set(value)) != len(value):
            raise ValueError("Hari kerja tidak boleh duplikat.")
        return sorted(value)

    # Cross-field checks live on the later field so the error points at it (``loc``).
    @field_validator("window_end")
    @classmethod
    def window_must_be_ordered(cls, value: time, info: ValidationInfo) -> time:
        start = info.data.get("window_start")
        if start is not None and start >= value:
            raise ValueError("Jam mulai harus lebih awal dari jam selesai.")
        return value

    @field_validator("throttle_max_s")
    @classmethod
    def throttle_must_be_ordered(cls, value: int, info: ValidationInfo) -> int:
        minimum = info.data.get("throttle_min_s")
        if minimum is not None and minimum > value:
            raise ValueError("Jeda minimum tidak boleh lebih besar dari jeda maksimum.")
        return value


class SettingsRead(SettingsWrite):
    model_config = ConfigDict(from_attributes=True)

    paused_reason: str | None
    consecutive_failures: int
    updated_at: datetime

    @field_validator("working_days", mode="before")
    @classmethod
    def working_days_from_column(cls, value: Any) -> Any:
        return parse_working_days(value) if isinstance(value, str) else value
