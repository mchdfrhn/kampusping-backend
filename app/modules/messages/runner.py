"""The worker loop (ADR 0003): plan, send, sleep, repeat.

One tick is the planning pass (``plan_messages``, which commits) followed by the send phase
(``run_send_phase``). Either part may fail without taking the worker down: the failure is logged
and the other part still runs, and the loop carries on after the sleep. Only
``asyncio.CancelledError`` and ``KeyboardInterrupt`` (both ``BaseException``) escape, so the
process can shut down cleanly.

Exactly one worker may run: the one-automatic-message-per-student-per-day check (AB-03) is only
safe with a single sender (ADR 0003).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.whatsapp.provider import WhatsAppProvider
from app.shared.clock import Clock

from .planner import PlanReport, plan_messages
from .sender import run_send_phase

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[int, int], float]
SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class TickReport:
    """What one tick did. ``plan`` is None when planning failed; counts only, no personal data."""

    plan: PlanReport | None
    handled: int

    def summary(self) -> str:
        if self.plan is None:
            return f"planning failed (see the log) handled={self.handled}"
        plan = self.plan
        return (
            f"created={plan.created} expired={plan.expired} superseded={plan.superseded} "
            f"escalated={plan.escalated} handled={self.handled}"
        )


async def run_tick(
    *,
    session_factory: SessionFactory,
    provider: WhatsAppProvider,
    clock: Clock,
    sleep: Sleep,
    jitter: Jitter,
) -> TickReport:
    """One planning pass and one send phase. Never raises on an ordinary error."""
    plan: PlanReport | None = None
    try:
        async with session_factory() as db:
            plan = await plan_messages(db, now=clock())
    except Exception:
        logger.exception("Worker tick: planning failed; the send phase still runs")

    handled = 0
    try:
        handled = await run_send_phase(
            session_factory, provider, clock=clock, sleep=sleep, jitter=jitter
        )
    except Exception:
        logger.exception("Worker tick: the send phase failed")

    report = TickReport(plan=plan, handled=handled)
    logger.info("Worker tick: %s", report.summary())
    return report


async def run_worker(
    *,
    session_factory: SessionFactory,
    provider: WhatsAppProvider,
    clock: Clock,
    sleep: Sleep,
    jitter: Jitter,
    tick_seconds: int,
    once: bool = False,
) -> None:
    """Tick forever, pausing ``tick_seconds`` between ticks; ``once`` runs one tick and returns."""
    while True:
        await run_tick(
            session_factory=session_factory,
            provider=provider,
            clock=clock,
            sleep=sleep,
            jitter=jitter,
        )
        if once:
            return
        await sleep(tick_seconds)
