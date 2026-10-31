from datetime import time

from sqlalchemy import Boolean, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditedModel

from .constants import (
    DEFAULT_CONSECUTIVE_FAILURE_LIMIT,
    DEFAULT_DAILY_CAP,
    DEFAULT_ESCALATION_OFFSET_DAYS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_THROTTLE_MAX_S,
    DEFAULT_THROTTLE_MIN_S,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DEFAULT_WORKING_DAYS,
)


class AppSettings(AuditedModel):
    """Application-wide settings. Holds exactly one row (id = 1)."""

    __tablename__ = "app_settings"

    campus_name: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    finance_contact: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    payment_link_siakad: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    payment_guide_sipema: Mapped[str] = mapped_column(String(1000), nullable=False, default="")

    window_start: Mapped[time] = mapped_column(Time, nullable=False, default=DEFAULT_WINDOW_START)
    window_end: Mapped[time] = mapped_column(Time, nullable=False, default=DEFAULT_WINDOW_END)
    # Comma-separated ISO weekdays, e.g. "1,2,3,4,5".
    working_days: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DEFAULT_WORKING_DAYS
    )

    auto_send_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_DAILY_CAP)
    throttle_min_s: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_THROTTLE_MIN_S
    )
    throttle_max_s: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_THROTTLE_MAX_S
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_MAX_ATTEMPTS)
    consecutive_failure_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_CONSECUTIVE_FAILURE_LIMIT
    )
    escalation_offset_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_ESCALATION_OFFSET_DAYS
    )

    # Set by the worker when sending stops (e.g. circuit breaker); cleared by "resume".
    paused_reason: Mapped[str | None] = mapped_column(String(500))
    # Internal circuit-breaker counter; not editable through the API.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
