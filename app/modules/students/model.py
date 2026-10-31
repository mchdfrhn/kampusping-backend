from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditedModel

from .constants import PHONE_MAX_LENGTH, PhoneStatus


class Student(AuditedModel):
    __tablename__ = "students"

    nim: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    study_program: Mapped[str | None] = mapped_column(String(150))
    cohort: Mapped[str | None] = mapped_column(String(20))
    # Not unique: siblings may share a parent's number.
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH))
    phone_status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=PhoneStatus.MISSING.value, index=True
    )
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opted_out_reason: Mapped[str | None] = mapped_column(String(500))
