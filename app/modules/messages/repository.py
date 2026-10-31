"""Set-based queries behind the planner. Everything here only flushes; the caller commits."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, insert, not_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.schedules.model import ScheduleRule
from app.modules.students.constants import PhoneStatus
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate

from .constants import SYSTEM_ACTOR, CancelReason, MessageKind, MessageStatus
from .model import Message

# Stay well below SQLite's bound-parameter limit when filtering by a list of ids.
ID_CHUNK_SIZE = 500


def _rowcount(result: Any) -> int:
    assert isinstance(result, CursorResult)  # noqa: S101 - narrows the type for pyright
    return max(result.rowcount, 0)


def is_exempt(today: date) -> ColumnElement[bool]:
    """An invoice is exempt while it has a reason and the exemption has not run out."""
    return and_(
        Invoice.exempt_reason.is_not(None),
        or_(Invoice.exempt_until.is_(None), Invoice.exempt_until >= today),
    )


def _to_escalate(today: date, escalation_offset_days: int) -> list[ColumnElement[bool]]:
    return [
        Invoice.status == InvoiceStatus.UNPAID.value,
        Invoice.deleted_at.is_(None),
        Invoice.escalated_at.is_(None),
        not_(is_exempt(today)),
        Invoice.due_date <= today - timedelta(days=escalation_offset_days),
    ]


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def active_rules(self) -> Sequence[ScheduleRule]:
        """Active, non-deleted rules whose template is also active and non-deleted."""
        stmt = (
            select(ScheduleRule)
            .join(ScheduleRule.template)
            .where(
                ScheduleRule.is_active.is_(True),
                ScheduleRule.deleted_at.is_(None),
                MessageTemplate.is_active.is_(True),
                MessageTemplate.deleted_at.is_(None),
            )
            .options(contains_eager(ScheduleRule.template))
            .order_by(ScheduleRule.offset_days, ScheduleRule.id)
        )
        return (await self.db.scalars(stmt)).all()

    async def candidates(
        self, rule: ScheduleRule, *, today: date, window_days: int
    ) -> Sequence[Invoice]:
        """Invoices (with their student loaded) due a message under ``rule`` that has none yet.

        ``rule_date = due_date + offset_days`` must fall in ``[today - window_days, today]``,
        expressed as a ``due_date`` range so the database does the filtering.
        """
        offset = timedelta(days=rule.offset_days)
        already_planned = exists().where(
            Message.invoice_id == Invoice.id, Message.rule_id == rule.id
        )
        stmt = (
            select(Invoice)
            .join(Invoice.student)
            .where(
                Invoice.status == InvoiceStatus.UNPAID.value,
                Invoice.deleted_at.is_(None),
                Invoice.escalated_at.is_(None),
                not_(is_exempt(today)),
                Invoice.due_date >= today - timedelta(days=window_days) - offset,
                Invoice.due_date <= today - offset,
                Student.deleted_at.is_(None),
                Student.opted_out_at.is_(None),
                Student.phone_status == PhoneStatus.VALID.value,
                ~already_planned,
            )
            .options(contains_eager(Invoice.student))
            .order_by(Invoice.id)
        )
        return (await self.db.scalars(stmt)).all()

    async def insert_messages(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Insert the rows with one ``executemany``; if a concurrent run planned any, keep the rest.

        Core inserts (not ORM objects) because the planner never needs the new ids and the ORM
        inserts one row at a time on dialects that cannot batch ``RETURNING``. Returns the number
        of rows inserted; duplicates of the ``invoice_id, rule_id`` unique key are skipped.
        """
        if not rows:
            return 0
        try:
            async with self.db.begin_nested():
                await self.db.execute(insert(Message), list(rows))
        except IntegrityError:
            return await self._insert_one_by_one(rows)
        return len(rows)

    async def _insert_one_by_one(self, rows: Sequence[Mapping[str, Any]]) -> int:
        inserted = 0
        for row in rows:
            try:
                async with self.db.begin_nested():
                    await self.db.execute(insert(Message).values(**row))
            except IntegrityError:
                continue
            inserted += 1
        return inserted

    async def cancel_queued_for_escalation(
        self, *, today: date, escalation_offset_days: int, now: datetime
    ) -> int:
        """Cancel queued auto messages of invoices that are about to be escalated."""
        due_for_escalation = select(Invoice.id).where(*_to_escalate(today, escalation_offset_days))
        result = await self.db.execute(
            update(Message)
            .where(
                Message.kind == MessageKind.AUTO.value,
                Message.status == MessageStatus.QUEUED.value,
                Message.invoice_id.in_(due_for_escalation),
            )
            .values(
                status=MessageStatus.CANCELLED.value,
                cancel_reason=CancelReason.ESCALATED.value,
                updated_at=now,
                updated_by=SYSTEM_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    async def escalate_invoices(
        self, *, today: date, escalation_offset_days: int, now: datetime
    ) -> int:
        result = await self.db.execute(
            update(Invoice)
            .where(*_to_escalate(today, escalation_offset_days))
            .values(escalated_at=now, updated_at=now, updated_by=SYSTEM_ACTOR)
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    async def expire_queued(self, *, today: date, window_days: int, now: datetime) -> int:
        result = await self.db.execute(
            update(Message)
            .where(
                Message.kind == MessageKind.AUTO.value,
                Message.status == MessageStatus.QUEUED.value,
                Message.scheduled_date < today - timedelta(days=window_days),
            )
            .values(
                status=MessageStatus.CANCELLED.value,
                cancel_reason=CancelReason.EXPIRED.value,
                updated_at=now,
                updated_by=SYSTEM_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    async def supersede_queued(self, *, now: datetime) -> int:
        """Per invoice keep only the queued auto message with the latest ``scheduled_date``.

        Ties go to the highest id. The others become ``cancelled(superseded)``.
        """
        queued_auto = (
            Message.kind == MessageKind.AUTO.value,
            Message.status == MessageStatus.QUEUED.value,
        )
        crowded = (
            select(Message.invoice_id)
            .where(*queued_auto)
            .group_by(Message.invoice_id)
            .having(func.count() > 1)
        )
        rows = await self.db.execute(
            select(Message.id, Message.invoice_id)
            .where(*queued_auto, Message.invoice_id.in_(crowded))
            .order_by(Message.invoice_id, Message.scheduled_date.desc(), Message.id.desc())
        )
        losers: list[int] = []
        previous_invoice: int | None = None
        for message_id, invoice_id in rows.all():
            if invoice_id == previous_invoice:
                losers.append(message_id)
            previous_invoice = invoice_id

        cancelled = 0
        for start in range(0, len(losers), ID_CHUNK_SIZE):
            result = await self.db.execute(
                update(Message)
                .where(Message.id.in_(losers[start : start + ID_CHUNK_SIZE]))
                .values(
                    status=MessageStatus.CANCELLED.value,
                    cancel_reason=CancelReason.SUPERSEDED.value,
                    updated_at=now,
                    updated_by=SYSTEM_ACTOR,
                )
                .execution_options(synchronize_session=False)
            )
            cancelled += _rowcount(result)
        return cancelled
