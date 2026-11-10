"""Rollback of an applied import, driven by its ``import_changes`` log.

``build_plan`` reads and checks (no writes); ``execute_plan`` writes. The caller owns the
transaction and decides whether conflicts block the rollback. Conflict reports carry only the
NIM / invoice code and field *names*, never values (personal data).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.modules.invoices.model import Invoice
from app.modules.messages.constants import SENT_STATUSES, CancelReason, MessageStatus
from app.modules.messages.model import Message
from app.modules.students.model import Student

from .changes import TRACKED_FIELDS, deserialize_value, serialize_fields
from .constants import ChangeAction, ChangeEntity
from .existing import _chunks
from .model import ImportChange

MSG_ROLLBACK_CANCEL_ERROR = "Impor dibatalkan"
MISSING = "missing"

Entities = dict[tuple[str, int], Student | Invoice]


@dataclass(frozen=True, slots=True)
class RollbackConflict:
    entity: str
    label: str  # NIM for a student, invoice code for an invoice
    fields: list[str]


@dataclass
class RollbackPlan:
    changes: list[ImportChange]
    entities: Entities
    conflicts: list[RollbackConflict] = field(default_factory=list)
    students_deleted: int = 0
    invoices_deleted: int = 0
    students_reverted: int = 0
    invoices_reverted: int = 0
    queued_messages: int = 0
    sent_messages: int = 0
    deleted_invoice_ids: list[int] = field(default_factory=list)


async def _load_entities(db: AsyncSession, changes: Sequence[ImportChange]) -> Entities:
    """Students and invoices named by the log, soft-deleted ones included."""
    entities: Entities = {}
    for kind, model in ((ChangeEntity.STUDENT, Student), (ChangeEntity.INVOICE, Invoice)):
        ids = sorted({c.entity_id for c in changes if c.entity == kind.value})
        for chunk in _chunks(ids):
            stmt = select(model).where(model.id.in_(chunk))
            if model is Invoice:
                stmt = stmt.options(noload(Invoice.student))
            for obj in await db.scalars(stmt):
                entities[(kind.value, obj.id)] = obj
    return entities


def _label(obj: Student | Invoice) -> str:
    return obj.nim if isinstance(obj, Student) else obj.invoice_code


async def _count_messages(
    db: AsyncSession, invoice_ids: Sequence[int], statuses: Sequence[str]
) -> int:
    total = 0
    for chunk in _chunks(invoice_ids):
        total += (
            await db.scalar(
                select(func.count(Message.id)).where(
                    Message.invoice_id.in_(chunk), Message.status.in_(statuses)
                )
            )
            or 0
        )
    return total


async def build_plan(db: AsyncSession, batch_id: int) -> RollbackPlan:
    """What rolling back ``batch_id`` would do, and what blocks it. Reads only."""
    changes = list(
        await db.scalars(
            select(ImportChange)
            .where(ImportChange.batch_id == batch_id, ImportChange.deleted_at.is_(None))
            .order_by(ImportChange.id)
        )
    )
    plan = RollbackPlan(changes=changes, entities=await _load_entities(db, changes))

    for change in changes:
        entity = ChangeEntity(change.entity)
        obj = plan.entities.get((change.entity, change.entity_id))
        if obj is None:
            plan.conflicts.append(RollbackConflict(change.entity, str(change.entity_id), [MISSING]))
            continue

        current = serialize_fields(entity, obj)
        conflicting = [
            name
            for name in TRACKED_FIELDS[entity]
            if name in change.after and current[name] != change.after[name]
        ]
        if conflicting:
            plan.conflicts.append(RollbackConflict(change.entity, _label(obj), conflicting))

        created = change.action == ChangeAction.CREATED.value
        if entity is ChangeEntity.STUDENT:
            plan.students_deleted += created
            plan.students_reverted += not created
        else:
            plan.invoices_deleted += created
            plan.invoices_reverted += not created
            if created:
                plan.deleted_invoice_ids.append(obj.id)

    plan.queued_messages = await _count_messages(
        db, plan.deleted_invoice_ids, [MessageStatus.QUEUED.value]
    )
    plan.sent_messages = await _count_messages(db, plan.deleted_invoice_ids, SENT_STATUSES)
    return plan


async def execute_plan(db: AsyncSession, plan: RollbackPlan, *, actor: str, now: datetime) -> None:
    """Soft-delete created rows, restore changed fields, cancel queued messages. Flushes only."""
    for change in plan.changes:
        obj = plan.entities.get((change.entity, change.entity_id))
        if obj is None:
            continue
        if change.action == ChangeAction.CREATED.value:
            obj.deleted_at = now
            obj.deleted_by = actor
        else:
            for name, value in (change.before or {}).items():
                setattr(obj, name, deserialize_value(name, value))
        obj.updated_by = actor
        obj.updated_at = now

    for chunk in _chunks(plan.deleted_invoice_ids):
        await db.execute(
            update(Message)
            .where(
                Message.invoice_id.in_(chunk),
                Message.status == MessageStatus.QUEUED.value,
            )
            .values(
                status=MessageStatus.CANCELLED.value,
                cancel_reason=CancelReason.MANUAL.value,
                error=MSG_ROLLBACK_CANCEL_ERROR,
                updated_by=actor,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
    await db.flush()
