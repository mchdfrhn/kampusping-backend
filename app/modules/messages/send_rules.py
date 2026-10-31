"""Rules of the send phase that need no provider: gates, the FR-06 re-check and error texts.

Split out of ``sender.py`` (which orchestrates claims, provider calls and outcomes).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.settings.model import AppSettings
from app.modules.settings.schema import parse_working_days
from app.modules.students.constants import PhoneStatus
from app.modules.students.model import Student
from app.modules.whatsapp.provider import ProviderError

from .constants import (
    ERROR_DELETED,
    ERROR_TOO_LONG,
    MAX_BODY_LENGTH,
    MAX_ERROR_LENGTH,
    CancelReason,
    MessageKind,
)
from .model import Message


@dataclass(frozen=True)
class SendSettings:
    """The settings one send step needs, read once at its start."""

    auto_send_enabled: bool
    paused: bool
    working_days: frozenset[int]
    window_start: time
    window_end: time
    daily_cap: int
    throttle_min_s: int
    throttle_max_s: int
    max_attempts: int
    consecutive_failure_limit: int

    @classmethod
    def of(cls, row: AppSettings) -> "SendSettings":
        return cls(
            auto_send_enabled=row.auto_send_enabled,
            paused=row.paused_reason is not None,
            working_days=frozenset(parse_working_days(row.working_days)),
            window_start=row.window_start,
            window_end=row.window_end,
            daily_cap=row.daily_cap,
            throttle_min_s=row.throttle_min_s,
            throttle_max_s=row.throttle_max_s,
            max_attempts=row.max_attempts,
            consecutive_failure_limit=row.consecutive_failure_limit,
        )


@dataclass(frozen=True)
class Verdict:
    """Why a claimed message must not be sent."""

    status: Literal["cancelled", "failed"]
    cancel_reason: str | None = None
    error: str | None = None


def local_day(local: datetime) -> tuple[datetime, datetime]:
    """The local calendar day containing ``local`` as a UTC interval ``[start, end)``."""
    start = datetime.combine(local.date(), time(0), tzinfo=local.tzinfo)
    end = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=local.tzinfo)
    return start.astimezone(UTC), end.astimezone(UTC)


def schedule_open(settings: SendSettings, local: datetime) -> bool:
    """Auto send on, not paused, a working day and inside ``[window_start, window_end)``."""
    if not settings.auto_send_enabled or settings.paused:
        return False
    if local.isoweekday() not in settings.working_days:
        return False
    return settings.window_start <= local.time() < settings.window_end


async def recheck(db: AsyncSession, message: Message, *, today: date) -> Verdict | None:
    """FR-06: re-load invoice and student and decide whether the message may still go out."""
    invoice = await db.scalar(
        select(Invoice)
        .where(Invoice.id == message.invoice_id)
        .options(noload(Invoice.student))
        .execution_options(populate_existing=True)
    )
    student = await db.scalar(
        select(Student)
        .where(Student.id == message.student_id)
        .execution_options(populate_existing=True)
    )
    if (
        invoice is None
        or student is None
        or invoice.deleted_at is not None
        or student.deleted_at is not None
    ):
        return Verdict("cancelled", CancelReason.MANUAL.value, ERROR_DELETED)
    if invoice.status == InvoiceStatus.PAID.value:
        return Verdict("cancelled", CancelReason.PAID.value)
    if student.opted_out_at is not None:
        return Verdict("cancelled", CancelReason.OPTED_OUT.value)
    if invoice.exempt_reason is not None and (
        invoice.exempt_until is None or invoice.exempt_until >= today
    ):
        return Verdict("cancelled", CancelReason.EXEMPT.value)
    if student.phone_status != PhoneStatus.VALID.value or not student.phone:
        return Verdict("cancelled", CancelReason.PHONE_INVALID.value)
    # The planner only cancels *queued* messages on escalation; one in retry must not go out.
    if message.kind == MessageKind.AUTO.value and invoice.escalated_at is not None:
        return Verdict("cancelled", CancelReason.ESCALATED.value)
    if len(message.body) > MAX_BODY_LENGTH:
        return Verdict("failed", error=ERROR_TOO_LONG)
    return None


def short_error(exc: Exception) -> str:
    """Stored error text. Provider errors carry fixed Indonesian messages; any other exception
    is reduced to its class name because its text may contain URLs, keys or personal data."""
    if isinstance(exc, ProviderError) and str(exc):
        text = str(exc)
    else:
        text = f"Gagal mengirim ({type(exc).__name__})."
    return text[:MAX_ERROR_LENGTH]
