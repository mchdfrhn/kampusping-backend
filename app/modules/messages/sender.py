"""The send phase of the worker (design spec section 5; ADR 0002, ADR 0003).

``run_send_phase`` runs once per worker tick. It first recovers messages a dead worker left in
``sending``, then repeats one send step until the queue is empty or a gate closes. One step
(``send_next``) works on a single message:

1. Gates, evaluated with the step's own ``now`` (so the window is re-checked after every throttle
   pause): auto send on, not paused, a working day, inside the send window (AB-04), provider
   connected, daily cap not reached. A closed gate means ``idle``.
2. Claim the next message atomically (``queued -> sending``).
3. Re-check it against the database (FR-06, AB-01, AB-05, AB-06, AB-07) and enforce one
   automatic message per student per day (AB-03).
4. Call the provider with no transaction open, record the outcome, then pause for the throttle.

Each database step is its own short transaction, so one bad message never poisons the rest. An
unexpected error after a claim (a bug, bad data) is logged with the message id, the message
becomes ``failed`` with a generic error and the phase moves on. An error outside a claim (for
example the database is down) ends the tick; nothing escapes ``run_send_phase``.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings as config
from app.modules.settings.service import SettingsService
from app.modules.students.model import Student
from app.modules.whatsapp.provider import (
    NotOnWhatsApp,
    ProviderError,
    ProviderRejected,
    ProviderUnavailable,
    SendResult,
    WhatsAppProvider,
)
from app.shared.clock import Clock, as_utc, local_now
from app.shared.phone import mask_phone

from .constants import (
    ERROR_INTERNAL,
    ERROR_NOT_ON_WHATSAPP,
    ERROR_REJECTED,
    RETRY_DELAYS_MINUTES,
    MessageKind,
    MessageStatus,
)
from .model import Message
from .send_repository import SendRepository
from .send_rules import SendSettings, local_day, recheck, schedule_open, short_error

logger = logging.getLogger(__name__)

SendOutcome = Literal["sent", "cancelled", "deferred", "retry", "failed", "idle"]
Sleep = Callable[[float], Awaitable[None]]
Jitter = Callable[[int, int], float]
SessionFactory = async_sessionmaker[AsyncSession]

# Losing the claim race this many times in a row ends the step (another worker is busy).
_CLAIM_TRIES = 5


@dataclass(frozen=True)
class _Ready:
    message_id: int
    student_id: int
    phone: str
    body: str
    attempts: int


@dataclass(frozen=True)
class _Step:
    outcome: SendOutcome
    stop: bool = False  # end the phase after this message
    called_provider: bool = False


_IDLE = _Step("idle")


async def send_next(
    session_factory: SessionFactory,
    provider: WhatsAppProvider,
    *,
    now: datetime,
    sleep: Sleep,
    jitter: Jitter,
) -> SendOutcome:
    """Claim and process at most one message; ``idle`` when nothing may be sent at ``now``.

    After a provider call it pauses for ``jitter(throttle_min_s, throttle_max_s)`` seconds,
    unless the phase has to stop anyway (provider unavailable, circuit breaker tripped).
    """
    step = await _send_step(session_factory, provider, now=now, sleep=sleep, jitter=jitter)
    return step.outcome


async def run_send_phase(
    session_factory: SessionFactory,
    provider: WhatsAppProvider,
    *,
    clock: Clock,
    sleep: Sleep,
    jitter: Jitter,
) -> int:
    """One worker tick of sending. Returns the number of messages handled (all but ``idle``)."""
    try:
        await _recover_stale(session_factory, now=as_utc(clock()))
    except Exception:
        logger.exception("Send phase: recovering stale messages failed; skipping this tick")
        return 0

    handled = 0
    while True:
        try:
            step = await _send_step(
                session_factory, provider, now=clock(), sleep=sleep, jitter=jitter
            )
        except Exception:
            logger.exception("Send phase stopped by an unexpected error")
            return handled
        if step.outcome == "idle":
            return handled
        handled += 1
        if step.stop:
            return handled


# --- One step --------------------------------------------------------------------------------


async def _send_step(
    session_factory: SessionFactory,
    provider: WhatsAppProvider,
    *,
    now: datetime,
    sleep: Sleep,
    jitter: Jitter,
) -> _Step:
    now = as_utc(now)
    local = local_now(now, config.app_timezone)
    settings = await _load_settings(session_factory)
    if not schedule_open(settings, local):
        return _IDLE
    if not await _provider_connected(provider):
        return _IDLE

    claimed = await _claim_and_recheck(session_factory, settings, now=now, local=local)
    if isinstance(claimed, _Step):
        return claimed
    step = await _deliver(session_factory, provider, claimed, settings, now=now)
    if step.called_provider and not step.stop:
        await sleep(jitter(settings.throttle_min_s, settings.throttle_max_s))
    return step


async def _load_settings(session_factory: SessionFactory) -> SendSettings:
    async with session_factory() as db:
        snapshot = SendSettings.of(await SettingsService(db).get())
        await db.commit()  # get() may have created the row
    return snapshot


async def _provider_connected(provider: WhatsAppProvider) -> bool:
    try:
        status = await provider.status()
    except ProviderError as exc:
        logger.info("Send phase skipped: provider status unavailable (%s)", type(exc).__name__)
        return False
    if status.state != "connected":
        logger.info("Send phase skipped: WhatsApp is %s", status.state)
        return False
    return True


async def _claim_and_recheck(
    session_factory: SessionFactory, settings: SendSettings, *, now: datetime, local: datetime
) -> _Step | _Ready:
    day_start, day_end = local_day(local)
    async with session_factory() as db:
        repo = SendRepository(db)
        if await repo.count_sent_between(day_start, day_end) >= settings.daily_cap:
            logger.info("Send phase: daily cap of %s reached", settings.daily_cap)
            return _IDLE
        message_id = await _claim(repo, now=now, today=local.date(), day=(day_start, day_end))
        if message_id is None:
            await db.commit()
            return _IDLE
        try:
            result = await _check_claimed(
                db, repo, message_id, now=now, today=local.date(), day=(day_start, day_end)
            )
            await db.commit()
            return result
        except Exception:
            logger.exception("Send phase: unexpected error on message %s", message_id)
            await db.rollback()
    async with session_factory() as db:
        await SendRepository(db).fail_unprocessed(message_id, now=now, error=ERROR_INTERNAL)
        await db.commit()
    return _Step("failed")


async def _claim(
    repo: SendRepository, *, now: datetime, today: date, day: tuple[datetime, datetime]
) -> int | None:
    for _ in range(_CLAIM_TRIES):
        candidate = await repo.next_candidate(
            now=now, today=today, day_start=day[0], day_end=day[1]
        )
        if candidate is None:
            return None
        if await repo.claim(candidate, now=now):
            return candidate
    return None


async def _check_claimed(
    db: AsyncSession,
    repo: SendRepository,
    message_id: int,
    *,
    now: datetime,
    today: date,
    day: tuple[datetime, datetime],
) -> _Step | _Ready:
    message = await db.get(Message, message_id, populate_existing=True)
    if message is None:  # pragma: no cover - the claim just saw the row
        return _IDLE
    verdict = await recheck(db, message, today=today)
    if verdict is not None:
        await repo.update_message(
            message_id,
            now=now,
            status=verdict.status,
            cancel_reason=verdict.cancel_reason,
            error=verdict.error,
            next_attempt_at=None,
        )
        logger.info(
            "Send phase: message %s %s (%s)",
            message_id,
            verdict.status,
            verdict.cancel_reason or verdict.error,
        )
        return _Step(verdict.status)
    if message.kind == MessageKind.AUTO.value and await repo.student_has_auto_message_today(
        message.student_id, message_id, day_start=day[0], day_end=day[1]
    ):
        await repo.update_message(message_id, now=now, status=MessageStatus.QUEUED.value)
        return _Step("deferred")
    student = await db.get(Student, message.student_id)
    if student is None or not student.phone:  # pragma: no cover - recheck() rules this out
        raise RuntimeError("claimed message has no sendable phone number")
    return _Ready(
        message_id=message_id,
        student_id=message.student_id,
        phone=student.phone,
        body=message.body,
        attempts=message.attempts,
    )


# --- Provider call and outcome ---------------------------------------------------------------


async def _deliver(
    session_factory: SessionFactory,
    provider: WhatsAppProvider,
    ready: _Ready,
    settings: SendSettings,
    *,
    now: datetime,
) -> _Step:
    try:
        result = await provider.send(
            phone=ready.phone, text=ready.body, client_ref=f"msg-{ready.message_id}"
        )
    except Exception as exc:
        error: Exception | None = exc
        result = None
    else:
        error = None
    try:
        async with session_factory() as db:
            repo = SendRepository(db)
            if error is None:
                assert result is not None  # noqa: S101 - narrows the type for pyright
                step = await _record_success(repo, ready, result, now=now)
            else:
                step = await _record_failure(repo, ready, error, settings, now=now)
            await db.commit()
    except Exception:
        # The message stays in ``sending``; stale recovery handles it after 10 minutes.
        logger.exception("Send phase: could not record the outcome of message %s", ready.message_id)
        raise
    return step


async def _record_success(
    repo: SendRepository, ready: _Ready, result: SendResult, *, now: datetime
) -> _Step:
    await repo.update_message(
        ready.message_id,
        now=now,
        status=MessageStatus.SENT.value,
        sent_at=now,
        provider_message_id=result.provider_message_id,
        attempts=ready.attempts + 1,
        error=None,
        next_attempt_at=None,
    )
    await repo.reset_failures()
    logger.info("Send phase: message %s sent to %s", ready.message_id, mask_phone(ready.phone))
    return _Step("sent", called_provider=True)


async def _record_failure(
    repo: SendRepository,
    ready: _Ready,
    exc: Exception,
    settings: SendSettings,
    *,
    now: datetime,
) -> _Step:
    masked = mask_phone(ready.phone)
    if isinstance(exc, ProviderUnavailable):
        # Not the message's fault: no attempt is burned and the phase stops for this tick.
        await repo.update_message(ready.message_id, now=now, status=MessageStatus.QUEUED.value)
        await _count_failure(repo, settings, now=now)
        logger.warning("Send phase: provider unavailable for message %s", ready.message_id)
        return _Step("retry", stop=True, called_provider=True)

    attempts = ready.attempts + 1
    if isinstance(exc, NotOnWhatsApp):
        await repo.update_message(
            ready.message_id,
            now=now,
            status=MessageStatus.FAILED.value,
            error=ERROR_NOT_ON_WHATSAPP,
            attempts=attempts,
            next_attempt_at=None,
        )
        await repo.mark_phone_invalid(ready.student_id, now=now)
        logger.info("Send phase: %s is not on WhatsApp (message %s)", masked, ready.message_id)
        return _Step("failed", called_provider=True)

    # A rejected request is permanent: it fails at once but, like any failure, counts.
    rejected = isinstance(exc, ProviderRejected)
    error = ERROR_REJECTED if rejected else short_error(exc)
    if not rejected and attempts < settings.max_attempts:
        delay = RETRY_DELAYS_MINUTES[min(attempts, len(RETRY_DELAYS_MINUTES)) - 1]
        await repo.update_message(
            ready.message_id,
            now=now,
            status=MessageStatus.QUEUED.value,
            error=error,
            attempts=attempts,
            next_attempt_at=now + timedelta(minutes=delay),
        )
        outcome: SendOutcome = "retry"
    else:
        await repo.update_message(
            ready.message_id,
            now=now,
            status=MessageStatus.FAILED.value,
            error=error,
            attempts=attempts,
            next_attempt_at=None,
        )
        outcome = "failed"
    logger.warning(
        "Send phase: message %s to %s failed (%s), attempt %s",
        ready.message_id,
        masked,
        type(exc).__name__,
        attempts,
    )
    tripped = await _count_failure(repo, settings, now=now)
    return _Step(outcome, stop=tripped, called_provider=True)


async def _count_failure(repo: SendRepository, settings: SendSettings, *, now: datetime) -> bool:
    tripped = await repo.record_failure(now=now, limit=settings.consecutive_failure_limit)
    if tripped:
        logger.error(
            "Send phase paused by the circuit breaker after %s consecutive failures",
            settings.consecutive_failure_limit,
        )
    return tripped


async def _recover_stale(session_factory: SessionFactory, *, now: datetime) -> None:
    async with session_factory() as db:
        sent, requeued = await SendRepository(db).recover_stale(now=now)
        await db.commit()
    if sent or requeued:
        logger.warning(
            "Send phase: recovered stale messages (%s assumed sent, %s requeued)", sent, requeued
        )
