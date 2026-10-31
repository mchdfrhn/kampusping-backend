"""The planner turns schedule rules into queued messages (ADR 0003).

It runs on a timer in the worker. Each run is idempotent: ``(invoice, rule)`` is planned at most
once, so running it twice (or after downtime) never duplicates a message. Order inside a run:
escalate, expire, plan, supersede.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as config
from app.modules.settings.service import SettingsService
from app.modules.templates.rendering import build_message_body
from app.shared.clock import as_utc, local_today

from .constants import PLANNING_WINDOW_DAYS, SYSTEM_ACTOR, MessageKind, MessageStatus
from .repository import MessageRepository


@dataclass(frozen=True)
class PlanReport:
    created: int
    expired: int
    superseded: int
    escalated: int


async def plan_messages(db: AsyncSession, *, now: datetime) -> PlanReport:
    """Run one planning pass and commit.

    ``now`` must be timezone-aware; any offset is accepted and normalised to UTC, the zone every
    stored timestamp uses (SQLite keeps only the wall-clock value).
    """
    now = as_utc(now)
    settings = await SettingsService(db).get()
    today = local_today(now, config.app_timezone)
    repo = MessageRepository(db)

    # Escalate first so escalated invoices are skipped by the candidate selection below.
    await repo.cancel_queued_for_escalation(
        today=today, escalation_offset_days=settings.escalation_offset_days, now=now
    )
    escalated = await repo.escalate_invoices(
        today=today, escalation_offset_days=settings.escalation_offset_days, now=now
    )
    expired = await repo.expire_queued(today=today, window_days=PLANNING_WINDOW_DAYS, now=now)

    created = 0
    for rule in await repo.active_rules():
        invoices = await repo.candidates(rule, today=today, window_days=PLANNING_WINDOW_DAYS)
        rows = [
            {
                "student_id": invoice.student_id,
                "invoice_id": invoice.id,
                "rule_id": rule.id,
                "kind": MessageKind.AUTO.value,
                "scheduled_date": invoice.due_date + timedelta(days=rule.offset_days),
                "body": build_message_body(
                    rule.template, student=invoice.student, invoice=invoice, settings=settings
                ),
                "status": MessageStatus.QUEUED.value,
                "queued_at": now,
                # Explicit, so the injected clock wins over the column defaults (real time).
                "created_at": now,
                "updated_at": now,
                "created_by": SYSTEM_ACTOR,
                "updated_by": SYSTEM_ACTOR,
            }
            for invoice in invoices
        ]
        created += await repo.insert_messages(rows)

    superseded = await repo.supersede_queued(now=now)
    await db.commit()
    return PlanReport(created=created, expired=expired, superseded=superseded, escalated=escalated)
