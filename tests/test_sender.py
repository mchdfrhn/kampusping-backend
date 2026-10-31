import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.permissions import ADMIN_ROLE
from app.db.base import Base
from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages import sender
from app.modules.messages.model import Message
from app.modules.messages.send_repository import SendRepository
from app.modules.messages.sender import run_send_phase, send_next
from app.modules.settings.model import AppSettings
from app.modules.settings.service import SettingsService
from app.modules.students.model import Student
from app.modules.whatsapp.fake import FakeProvider
from app.modules.whatsapp.provider import (
    NotOnWhatsApp,
    ProviderRejected,
    ProviderStatus,
    ProviderUnavailable,
)
from app.shared.utils import ensure_utc

from .helpers import create_user

Factory = async_sessionmaker[AsyncSession]

# Monday 16 November 2026, 10:00 in Jakarta (UTC+7).
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
TODAY = date(2026, 11, 16)
TUESDAY = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
BODY = "Halo Budi, tagihan Rp1.250.000 jatuh tempo 16 November 2026.\n\nBalas STOP untuk berhenti."


# --- Test doubles --------------------------------------------------------------------------


class FakeClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Sleeper:
    """Records requested pauses; optionally moves the clock forward while "sleeping"."""

    def __init__(self, clock: FakeClock | None = None, advance: timedelta = timedelta(0)) -> None:
        self.calls: list[float] = []
        self._clock = clock
        self._advance = advance

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.now += self._advance


class Jitter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, low: int, high: int) -> float:
        self.calls.append((low, high))
        return low + (high - low) * 0.25


class UnreachableProvider(FakeProvider):
    async def status(self) -> ProviderStatus:
        raise ProviderUnavailable("Gateway WhatsApp tidak tersedia.")


# --- Fixtures and helpers ------------------------------------------------------------------


def factory_or_default(factory: Factory | None) -> Factory:
    return factory or get_session_factory()


async def configure(factory: Factory | None = None, **values: Any) -> None:
    async with factory_or_default(factory)() as db:
        row = await SettingsService(db).get()
        for field, value in values.items():
            setattr(row, field, value)
        await db.commit()


@pytest.fixture(autouse=True)
async def sending_enabled(database: None) -> None:
    await configure(auto_send_enabled=True)


_phone_counter: Iterator[int] = iter(range(10, 10_000))


async def add_student(
    *,
    phone: str | None = None,
    phone_status: str = "valid",
    factory: Factory | None = None,
) -> int:
    number = next(_phone_counter)
    async with factory_or_default(factory)() as db:
        student = Student(
            nim=f"2021{number:04d}",
            name="Budi Santoso",
            phone=phone or f"+628123456{number:04d}",
            phone_status=phone_status,
        )
        db.add(student)
        await db.commit()
        return student.id


async def add_invoice(
    student_id: int, *, due: date = TODAY, factory: Factory | None = None, **values: Any
) -> int:
    number = next(_phone_counter)
    async with factory_or_default(factory)() as db:
        invoice = Invoice(
            invoice_code=f"INV-{number}",
            student_id=student_id,
            semester="Ganjil 2026/2027",
            amount=1_250_000,
            due_date=due,
            status="unpaid",
            source="siakad",
            **values,
        )
        db.add(invoice)
        await db.commit()
        return invoice.id


async def add_message(
    invoice_id: int,
    student_id: int,
    *,
    scheduled: date = TODAY,
    kind: str = "auto",
    status: str = "queued",
    body: str = BODY,
    factory: Factory | None = None,
    **values: Any,
) -> int:
    async with factory_or_default(factory)() as db:
        message = Message(
            student_id=student_id,
            invoice_id=invoice_id,
            kind=kind,
            scheduled_date=scheduled,
            body=body,
            status=status,
            queued_at=NOW - timedelta(days=1),
            **values,
        )
        db.add(message)
        await db.commit()
        return message.id


async def queued_message(*, factory: Factory | None = None, **values: Any) -> int:
    """A fresh student with one unpaid invoice and one queued auto message."""
    student_id = await add_student(factory=factory)
    invoice_id = await add_invoice(student_id, factory=factory)
    return await add_message(invoice_id, student_id, factory=factory, **values)


async def get_message(message_id: int) -> Message:
    async with get_session_factory()() as db:
        message = await db.get(Message, message_id)
        assert message is not None
        return message


async def get_student(student_id: int) -> Student:
    async with get_session_factory()() as db:
        student = await db.get(Student, student_id)
        assert student is not None
        return student


async def get_settings() -> AppSettings:
    async with get_session_factory()() as db:
        row = await db.get(AppSettings, 1)
        assert row is not None
        return row


async def update_row(model: type[Any], row_id: int, **values: Any) -> None:
    async with get_session_factory()() as db:
        row = await db.get(model, row_id)
        assert row is not None
        for field, value in values.items():
            setattr(row, field, value)
        await db.commit()


async def run(
    provider: FakeProvider,
    clock: FakeClock | None = None,
    sleep: Sleeper | None = None,
    jitter: Jitter | None = None,
) -> int:
    return await run_send_phase(
        get_session_factory(),
        provider,
        clock=clock or FakeClock(),
        sleep=sleep or Sleeper(),
        jitter=jitter or Jitter(),
    )


def at_wib(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC) - timedelta(
        hours=7
    )


# --- Happy path ----------------------------------------------------------------------------


async def test_sends_a_queued_message_and_records_the_provider_id() -> None:
    message_id = await queued_message()
    student = await get_student((await get_message(message_id)).student_id)
    fake = FakeProvider()

    handled = await run(fake)

    assert handled == 1
    (sent,) = fake.sent
    assert sent.phone == student.phone
    assert sent.text == BODY
    assert sent.client_ref == f"msg-{message_id}"
    message = await get_message(message_id)
    assert message.status == "sent"
    assert message.provider_message_id == sent.provider_message_id
    assert message.sent_at is not None
    assert ensure_utc(message.sent_at) == NOW
    assert message.attempts == 1
    assert message.error is None
    assert message.updated_by == "system"


async def test_send_next_returns_idle_when_the_queue_is_empty() -> None:
    outcome = await send_next(
        get_session_factory(), FakeProvider(), now=NOW, sleep=Sleeper(), jitter=Jitter()
    )
    assert outcome == "idle"


async def test_message_scheduled_for_a_later_day_waits() -> None:
    message_id = await queued_message(scheduled=TODAY + timedelta(days=1))
    fake = FakeProvider()

    assert await run(fake) == 0
    assert fake.sent == []
    assert (await get_message(message_id)).status == "queued"


# --- Gates ---------------------------------------------------------------------------------


async def test_nothing_sent_when_auto_send_disabled() -> None:
    await configure(auto_send_enabled=False)
    message_id = await queued_message()
    fake = FakeProvider()

    assert await run(fake) == 0
    assert fake.sent == []
    assert (await get_message(message_id)).status == "queued"


@pytest.mark.parametrize(
    ("now", "expect_sent"),
    [
        (at_wib(TODAY, 7, 59), False),
        (at_wib(TODAY, 8, 0), True),
        (at_wib(TODAY, 16, 59, 59), True),
        (at_wib(TODAY, 17, 0), False),
        (at_wib(TODAY, 17, 1), False),
        (at_wib(date(2026, 11, 21), 10, 0), False),  # Saturday
        (at_wib(date(2026, 11, 22), 10, 0), False),  # Sunday
    ],
    ids=["07:59", "08:00", "16:59:59", "17:00", "17:01", "saturday", "sunday"],
)
async def test_nothing_sent_outside_window(now: datetime, expect_sent: bool) -> None:
    message_id = await queued_message()
    fake = FakeProvider()

    handled = await run(fake, FakeClock(now))

    assert handled == (1 if expect_sent else 0)
    assert len(fake.sent) == (1 if expect_sent else 0)
    assert (await get_message(message_id)).status == ("sent" if expect_sent else "queued")


async def test_window_and_working_days_come_from_settings() -> None:
    await configure(window_start=time(6, 0), window_end=time(7, 0), working_days="6")
    message_id = await queued_message(scheduled=date(2026, 11, 21))
    fake = FakeProvider()

    assert await run(fake, FakeClock(at_wib(date(2026, 11, 21), 6, 30))) == 1  # Saturday 06:30
    assert (await get_message(message_id)).status == "sent"


async def test_window_is_rechecked_after_throttle_sleep() -> None:
    ids = [await queued_message() for _ in range(3)]
    clock = FakeClock(at_wib(TODAY, 16, 59, 50))
    sleeper = Sleeper(clock, advance=timedelta(seconds=20))  # wakes up at 17:00:10
    fake = FakeProvider()

    handled = await run(fake, clock, sleeper)

    assert handled == 1
    assert len(fake.sent) == 1
    assert len(sleeper.calls) == 1
    statuses = [(await get_message(i)).status for i in ids]
    assert statuses == ["sent", "queued", "queued"]


async def test_daily_cap_stops_sending_and_counts_local_day() -> None:
    await configure(daily_cap=2)
    other = await add_student()
    other_invoice = await add_invoice(other)
    # 00:30 WIB on 16 November is still 15 November in UTC: it counts towards today's cap.
    await add_message(
        other_invoice,
        other,
        status="sent",
        sent_at=datetime(2026, 11, 15, 17, 30, tzinfo=UTC),
    )
    # 23:30 WIB on 15 November: yesterday in Jakarta, so it does not count.
    await add_message(
        other_invoice,
        other,
        kind="manual",
        status="delivered",
        sent_at=datetime(2026, 11, 15, 16, 30, tzinfo=UTC),
    )
    first = await queued_message()
    second = await queued_message()
    fake = FakeProvider()

    handled = await run(fake)

    assert handled == 1
    assert len(fake.sent) == 1
    assert (await get_message(first)).status == "sent"
    assert (await get_message(second)).status == "queued"


async def test_disconnected_provider_skips_the_phase_and_changes_no_counters() -> None:
    await configure(consecutive_failures=2)
    message_id = await queued_message()

    for provider in (FakeProvider(state="qr"), FakeProvider(state="connecting")):
        assert await run(provider) == 0
        assert provider.sent == []
    unreachable = UnreachableProvider()
    assert await run(unreachable) == 0

    message = await get_message(message_id)
    assert message.status == "queued"
    assert message.attempts == 0
    settings = await get_settings()
    assert settings.consecutive_failures == 2
    assert settings.paused_reason is None


# --- Re-check before sending (FR-06) -------------------------------------------------------


async def test_paid_invoice_is_cancelled_not_sent() -> None:
    message_id = await queued_message()
    await update_row(Invoice, (await get_message(message_id)).invoice_id, status="paid")
    fake = FakeProvider()
    sleeper = Sleeper()

    assert await run(fake, sleep=sleeper) == 1

    message = await get_message(message_id)
    assert message.status == "cancelled"
    assert message.cancel_reason == "paid"
    assert message.attempts == 0
    assert fake.sent == []
    assert sleeper.calls == []


@pytest.mark.parametrize(
    "reason",
    [
        "opted_out",
        "exempt",
        "exempt_forever",
        "phone_invalid",
        "phone_missing",
        "escalated",
        "deleted_student",
        "deleted_invoice",
    ],
)
async def test_recheck_cancels_without_sending(reason: str) -> None:
    message_id = await queued_message()
    message = await get_message(message_id)
    student_changes: dict[str, Any] = {
        "opted_out": {"opted_out_at": NOW - timedelta(hours=1)},
        "phone_invalid": {"phone_status": "invalid"},
        "phone_missing": {"phone": None, "phone_status": "missing"},
        "deleted_student": {"deleted_at": NOW - timedelta(hours=1)},
    }.get(reason, {})
    invoice_changes: dict[str, Any] = {
        "exempt": {"exempt_reason": "Cicilan", "exempt_until": TODAY},
        "exempt_forever": {"exempt_reason": "Beasiswa"},
        "escalated": {"escalated_at": NOW - timedelta(hours=1)},
        "deleted_invoice": {"deleted_at": NOW - timedelta(hours=1)},
    }.get(reason, {})
    if student_changes:
        await update_row(Student, message.student_id, **student_changes)
    if invoice_changes:
        await update_row(Invoice, message.invoice_id, **invoice_changes)
    fake = FakeProvider()
    sleeper = Sleeper()

    assert await run(fake, sleep=sleeper) == 1

    expected_reason = {
        "exempt_forever": "exempt",
        "phone_missing": "phone_invalid",
        "deleted_student": "manual",
        "deleted_invoice": "manual",
    }.get(reason, reason)
    message = await get_message(message_id)
    assert message.status == "cancelled"
    assert message.cancel_reason == expected_reason
    if reason.startswith("deleted"):
        assert message.error == "Mahasiswa atau tagihan sudah dihapus."
    assert fake.sent == []
    assert sleeper.calls == []


async def test_expired_exemption_is_sent() -> None:
    message_id = await queued_message()
    await update_row(
        Invoice,
        (await get_message(message_id)).invoice_id,
        exempt_reason="Cicilan",
        exempt_until=TODAY - timedelta(days=1),
    )
    fake = FakeProvider()

    assert await run(fake) == 1
    assert (await get_message(message_id)).status == "sent"


async def test_manual_message_ignores_escalation_and_the_one_per_day_rule() -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(student_id, escalated_at=NOW - timedelta(days=1))
    await add_message(invoice_id, student_id, status="sent", sent_at=NOW - timedelta(hours=1))
    manual = await add_message(invoice_id, student_id, kind="manual")
    fake = FakeProvider()

    assert await run(fake) == 1
    assert (await get_message(manual)).status == "sent"


async def test_message_longer_than_4096_fails_without_calling_the_provider() -> None:
    too_long = await queued_message(body="x" * 4097)
    just_fits = await queued_message(body="y" * 4096)
    fake = FakeProvider()

    assert await run(fake) == 2

    failed = await get_message(too_long)
    assert failed.status == "failed"
    assert failed.error == "Pesan terlalu panjang."
    assert failed.attempts == 0
    assert [sent.client_ref for sent in fake.sent] == [f"msg-{just_fits}"]
    assert (await get_settings()).consecutive_failures == 0


# --- One automatic message per student per day (AB-03) -------------------------------------


async def test_one_automatic_message_per_student_per_day_and_second_is_deferred_not_lost() -> None:
    student_id = await add_student()
    newer_invoice = await add_invoice(student_id, due=date(2026, 11, 14))
    older_invoice = await add_invoice(student_id, due=date(2026, 11, 10))
    # The newer invoice's message is queued first (lower id): the older due date must still win.
    newer = await add_message(newer_invoice, student_id)
    older = await add_message(older_invoice, student_id)
    fake = FakeProvider()

    assert await run(fake) == 1
    assert [sent.client_ref for sent in fake.sent] == [f"msg-{older}"]
    waiting = await get_message(newer)
    assert waiting.status == "queued"
    assert waiting.cancel_reason is None
    assert waiting.attempts == 0

    # Later the same day: still nothing for this student.
    assert await run(fake, FakeClock(NOW + timedelta(hours=4))) == 0
    assert len(fake.sent) == 1

    # Next working day it goes out.
    assert await run(fake, FakeClock(TUESDAY)) == 1
    assert [sent.client_ref for sent in fake.sent] == [f"msg-{older}", f"msg-{newer}"]
    assert (await get_message(newer)).status == "sent"


async def test_message_sent_yesterday_in_jakarta_does_not_block_today() -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(student_id)
    # 23:30 WIB on Sunday 15 November.
    await add_message(
        invoice_id, student_id, status="read", sent_at=datetime(2026, 11, 15, 16, 30, tzinfo=UTC)
    )
    queued = await add_message(invoice_id, student_id)
    fake = FakeProvider()

    assert await run(fake) == 1
    assert (await get_message(queued)).status == "sent"


async def test_other_students_are_sent_while_one_waits_for_tomorrow() -> None:
    student_id = await add_student()
    invoice_a = await add_invoice(student_id)
    invoice_b = await add_invoice(student_id)
    first = await add_message(invoice_a, student_id)
    second = await add_message(invoice_b, student_id)
    other = await queued_message()
    fake = FakeProvider()

    assert await run(fake) == 2
    assert {sent.client_ref for sent in fake.sent} == {f"msg-{first}", f"msg-{other}"}
    assert (await get_message(second)).status == "queued"


# --- Throttle ------------------------------------------------------------------------------


async def test_sleeps_between_messages_within_configured_range() -> None:
    await configure(throttle_min_s=8, throttle_max_s=20)
    for _ in range(3):
        await queued_message()
    sleeper = Sleeper()
    jitter = Jitter()
    fake = FakeProvider()

    assert await run(fake, sleep=sleeper, jitter=jitter) == 3

    assert len(fake.sent) == 3
    assert jitter.calls == [(8, 20)] * 3
    assert sleeper.calls == [11.0] * 3  # one pause after every provider call
    assert all(8 <= seconds <= 20 for seconds in sleeper.calls)


# --- Errors --------------------------------------------------------------------------------


async def test_provider_failure_retries_with_5_15_45_minute_backoff_then_fails() -> None:
    await configure(max_attempts=4)
    message_id = await queued_message()
    fake = FakeProvider()
    fake.fail_next(RuntimeError("boom with secret-token"), times=4)
    clock = FakeClock(NOW)

    expected_waits = [timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=45)]
    for attempt, wait in enumerate(expected_waits, start=1):
        assert await run(fake, clock) == 1
        message = await get_message(message_id)
        assert message.status == "queued"
        assert message.attempts == attempt
        assert message.next_attempt_at is not None
        assert ensure_utc(message.next_attempt_at) == clock.now + wait

        # Not picked up again before the back-off has passed.
        assert await run(fake, FakeClock(clock.now + wait - timedelta(seconds=1))) == 0
        clock.now += wait

    assert await run(fake, clock) == 1
    message = await get_message(message_id)
    assert message.status == "failed"
    assert message.attempts == 4
    assert message.error is not None
    assert "RuntimeError" in message.error
    assert "secret-token" not in message.error
    assert len(message.error) <= 200
    assert fake.sent == []


async def test_default_max_attempts_fails_on_the_third_attempt() -> None:
    message_id = await queued_message()
    fake = FakeProvider()
    fake.fail_next(RuntimeError("boom"), times=3)
    clock = FakeClock(NOW)

    await run(fake, clock)
    clock.now += timedelta(minutes=5)
    await run(fake, clock)
    clock.now += timedelta(minutes=15)
    await run(fake, clock)

    message = await get_message(message_id)
    assert message.status == "failed"
    assert message.attempts == 3


async def test_not_on_whatsapp_fails_immediately_and_marks_student_invalid() -> None:
    student_id = await add_student()
    first_invoice = await add_invoice(student_id, due=date(2026, 11, 10))
    second_invoice = await add_invoice(student_id, due=date(2026, 11, 12))
    first = await add_message(first_invoice, student_id)
    second = await add_message(second_invoice, student_id)
    student = await get_student(student_id)
    assert student.phone is not None
    fake = FakeProvider()
    fake.unregistered.add(student.phone)

    assert await run(fake) == 2

    failed = await get_message(first)
    assert failed.status == "failed"
    assert failed.error == "Nomor tidak terdaftar di WhatsApp."
    assert failed.attempts == 1
    assert (await get_student(student_id)).phone_status == "invalid"
    follow_up = await get_message(second)  # re-check sees the invalid number
    assert follow_up.status == "cancelled"
    assert follow_up.cancel_reason == "phone_invalid"
    assert (await get_settings()).consecutive_failures == 0


async def test_provider_rejected_fails_immediately_and_counts_as_failure() -> None:
    message_id = await queued_message()
    other = await queued_message()
    fake = FakeProvider()
    fake.fail_next(ProviderRejected("anything the provider says"))

    assert await run(fake) == 2

    message = await get_message(message_id)
    assert message.status == "failed"
    assert message.error == "Permintaan ditolak gateway."
    assert message.attempts == 1
    assert message.next_attempt_at is None
    assert (await get_message(other)).status == "sent"  # the queue carries on


async def test_provider_rejected_counts_towards_the_breaker() -> None:
    await configure(consecutive_failures=4)
    await queued_message()
    untouched = await queued_message()
    fake = FakeProvider()
    fake.fail_next(ProviderRejected("Permintaan ditolak gateway."))

    assert await run(fake) == 1
    assert (await get_settings()).paused_reason == "circuit_breaker"
    assert (await get_message(untouched)).status == "queued"


async def test_one_failure_does_not_stop_the_rest() -> None:
    failing = await queued_message()
    healthy = await queued_message()
    fake = FakeProvider()
    fake.fail_next(RuntimeError("boom"))

    assert await run(fake) == 2

    assert (await get_message(failing)).status == "queued"  # waiting for its retry
    assert (await get_message(failing)).attempts == 1
    assert (await get_message(healthy)).status == "sent"
    assert [sent.client_ref for sent in fake.sent] == [f"msg-{healthy}"]
    assert (await get_settings()).consecutive_failures == 0  # the success reset the counter


async def test_circuit_breaker_after_5_consecutive_failures_sets_paused_reason_and_stops() -> None:
    ids = [await queued_message() for _ in range(7)]
    fake = FakeProvider()
    fake.fail_next(RuntimeError("boom"), times=10)
    sleeper = Sleeper()

    assert await run(fake, sleep=sleeper) == 5

    settings = await get_settings()
    assert settings.paused_reason == "circuit_breaker"
    assert settings.consecutive_failures == 5
    attempts = [(await get_message(i)).attempts for i in ids]
    assert attempts == [1, 1, 1, 1, 1, 0, 0]
    assert len(sleeper.calls) == 4  # no pause after the failure that tripped the breaker

    # Paused: the phase refuses to run, even with a healthy provider.
    healthy = FakeProvider()
    assert await run(healthy) == 0
    assert healthy.sent == []

    admin = await create_user("admin@example.com", ADMIN_ROLE)
    async with get_session_factory()() as db:
        await SettingsService(db).resume(admin)

    assert await run(healthy) == 2  # the untouched two; the others wait for their back-off
    assert [sent.client_ref for sent in healthy.sent] == [f"msg-{ids[5]}", f"msg-{ids[6]}"]
    assert (await get_settings()).paused_reason is None


async def test_provider_unavailable_stops_the_phase_without_burning_attempts() -> None:
    first = await queued_message()
    second = await queued_message()
    fake = FakeProvider()
    fake.fail_next(ProviderUnavailable("WhatsApp belum terhubung."))
    sleeper = Sleeper()

    assert await run(fake, sleep=sleeper) == 1

    message = await get_message(first)
    assert message.status == "queued"
    assert message.attempts == 0
    assert message.next_attempt_at is None
    assert (await get_message(second)).status == "queued"
    assert (await get_message(second)).attempts == 0
    assert (await get_settings()).consecutive_failures == 1
    assert fake.sent == []
    assert sleeper.calls == []

    assert await run(fake) == 2
    assert len(fake.sent) == 2


async def test_provider_unavailable_counts_towards_the_breaker() -> None:
    await configure(consecutive_failures=4)
    await queued_message()
    fake = FakeProvider()
    fake.fail_next(ProviderUnavailable("WhatsApp belum terhubung."))

    assert await run(fake) == 1
    assert (await get_settings()).paused_reason == "circuit_breaker"


async def test_success_resets_consecutive_failures() -> None:
    await configure(consecutive_failures=4)
    await queued_message()
    fake = FakeProvider()

    assert await run(fake) == 1
    assert (await get_settings()).consecutive_failures == 0

    await queued_message()  # a fresh message that fails: the counter starts again from zero
    fake.fail_next(RuntimeError("boom"))
    assert await run(fake) == 1
    settings = await get_settings()
    assert settings.consecutive_failures == 1
    assert settings.paused_reason is None


async def test_attempts_incremented_once_per_provider_call() -> None:
    fake = FakeProvider()
    succeeds = await queued_message()

    assert await run(fake) == 1
    assert (await get_message(succeeds)).attempts == 1

    retried = await queued_message()  # fails twice, then goes through on the third call
    fake.fail_next(RuntimeError("boom"), times=2)
    clock = FakeClock(NOW)
    assert await run(fake, clock) == 1
    assert (await get_message(retried)).attempts == 1
    clock.now += timedelta(minutes=5)
    assert await run(fake, clock) == 1
    assert (await get_message(retried)).attempts == 2
    clock.now += timedelta(minutes=15)
    assert await run(fake, clock) == 1
    message = await get_message(retried)
    assert message.status == "sent"
    assert message.attempts == 3
    assert (await get_message(succeeds)).attempts == 1  # untouched by the other message's retries


async def test_stale_sending_message_is_requeued_after_10_minutes_unless_provider_id_set() -> None:
    await configure(auto_send_enabled=False)  # observe the recovery on its own
    eleven_minutes_ago = NOW - timedelta(minutes=11)
    lost = await queued_message(status="sending", updated_at=eleven_minutes_ago)
    delivered = await queued_message(
        status="sending", updated_at=eleven_minutes_ago, provider_message_id="wamid-9"
    )
    in_flight = await queued_message(status="sending", updated_at=NOW - timedelta(minutes=9))

    assert await run(FakeProvider()) == 0

    requeued = await get_message(lost)
    assert requeued.status == "queued"
    assert requeued.sent_at is None
    assert ensure_utc(requeued.updated_at) == NOW
    assumed_sent = await get_message(delivered)
    assert assumed_sent.status == "sent"
    assert assumed_sent.sent_at is not None
    assert ensure_utc(assumed_sent.sent_at) == eleven_minutes_ago
    assert (await get_message(in_flight)).status == "sending"


async def test_requeued_stale_message_is_sent_in_the_same_phase() -> None:
    lost = await queued_message(status="sending", updated_at=NOW - timedelta(minutes=30))
    fake = FakeProvider()

    assert await run(fake) == 1
    assert (await get_message(lost)).status == "sent"


async def test_unexpected_error_is_logged_and_does_not_escape_the_phase(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    broken = await queued_message()
    healthy = await queued_message()

    original = sender.recheck

    async def explode(db: AsyncSession, message: Message, *, today: date) -> Any:
        if message.id == broken:
            raise RuntimeError("database went away")
        return await original(db, message, today=today)

    monkeypatch.setattr(sender, "recheck", explode)
    fake = FakeProvider()
    caplog.set_level(logging.ERROR, logger="app.modules.messages.sender")

    assert await run(fake) == 2

    failed = await get_message(broken)
    assert failed.status == "failed"
    assert failed.error == "Terjadi kesalahan internal saat memproses pesan."
    assert "database went away" not in (failed.error or "")
    assert (await get_message(healthy)).status == "sent"
    assert any(f"message {broken}" in record.getMessage() for record in caplog.records)


async def test_phone_is_never_logged_unmasked(caplog: pytest.LogCaptureFixture) -> None:
    first = await queued_message()
    second = await queued_message()
    phones = [(await get_student((await get_message(i)).student_id)).phone for i in (first, second)]
    fake = FakeProvider()
    fake.fail_next(NotOnWhatsApp("Nomor tidak terdaftar di WhatsApp."))
    caplog.set_level(logging.DEBUG, logger="app.modules.messages.sender")

    await run(fake)

    logged = " | ".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app.modules.messages.sender"
    )
    assert logged  # something was logged
    for phone in phones:
        assert phone is not None
        assert phone not in logged
        assert phone.removeprefix("+") not in logged
        assert phone[-4:] in logged


# --- Daily cap and soft deletes ------------------------------------------------------------


async def test_daily_cap_counts_a_sending_message_without_sent_at() -> None:
    await configure(daily_cap=1)
    other = await add_student()
    other_invoice = await add_invoice(other)
    # In flight right now: ``sent_at`` is only written when the outcome is recorded.
    await add_message(other_invoice, other, status="sending", updated_at=NOW)
    waiting = await queued_message()
    fake = FakeProvider()

    assert await run(fake) == 0
    assert fake.sent == []
    assert (await get_message(waiting)).status == "queued"


async def test_soft_deleted_message_is_never_sent() -> None:
    message_id = await queued_message(deleted_at=NOW - timedelta(hours=1))
    fake = FakeProvider()

    assert await run(fake) == 0
    assert fake.sent == []
    assert (await get_message(message_id)).status == "queued"


async def test_claim_refuses_a_soft_deleted_message() -> None:
    message_id = await queued_message(deleted_at=NOW - timedelta(hours=1))

    async with get_session_factory()() as db:
        assert await SendRepository(db).claim(message_id, now=NOW) is False
        await db.commit()

    assert (await get_message(message_id)).status == "queued"


async def test_candidate_query_excludes_soft_deleted_messages() -> None:
    deleted = await queued_message(deleted_at=NOW - timedelta(hours=1))  # lowest id: would win
    live = await queued_message()
    day = (NOW - timedelta(hours=10), NOW + timedelta(hours=14))

    async with get_session_factory()() as db:
        repo = SendRepository(db)
        picked = await repo.next_candidate(now=NOW, today=TODAY, day_start=day[0], day_end=day[1])
        assert picked == live
        assert picked != deleted
        await update_row(Message, live, deleted_at=NOW)
        assert (
            await SendRepository(db).next_candidate(
                now=NOW, today=TODAY, day_start=day[0], day_end=day[1]
            )
            is None
        )


# --- Outcome writes never overwrite someone else's state -----------------------------------


async def test_success_outcome_does_not_overwrite_a_cancelled_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    message_id = await queued_message()
    fake = FakeProvider()
    original = fake.send

    async def cancel_then_send(**kwargs: Any) -> Any:
        await update_row(Message, message_id, status="cancelled", cancel_reason="manual")
        return await original(**kwargs)

    monkeypatch.setattr(fake, "send", cancel_then_send)
    caplog.set_level(logging.WARNING, logger="app.modules.messages.sender")

    assert await run(fake) == 1  # the phase carries on; nothing raised

    message = await get_message(message_id)
    assert message.status == "cancelled"
    assert message.cancel_reason == "manual"
    assert message.sent_at is None
    assert message.provider_message_id is None
    assert message.attempts == 0
    assert any(
        record.levelno == logging.WARNING and f"message {message_id}" in record.getMessage()
        for record in caplog.records
    )


async def test_failure_outcome_does_not_overwrite_a_cancelled_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    message_id = await queued_message()
    fake = FakeProvider()

    async def cancel_then_fail(**kwargs: Any) -> Any:
        await update_row(Message, message_id, status="cancelled", cancel_reason="manual")
        raise RuntimeError("boom")

    monkeypatch.setattr(fake, "send", cancel_then_fail)
    caplog.set_level(logging.WARNING, logger="app.modules.messages.sender")

    assert await run(fake) == 1

    message = await get_message(message_id)
    assert message.status == "cancelled"
    assert message.attempts == 0
    assert message.error is None
    assert message.next_attempt_at is None
    assert any(
        record.levelno == logging.WARNING and f"message {message_id}" in record.getMessage()
        for record in caplog.records
    )


# --- Concurrency ---------------------------------------------------------------------------


async def test_claim_is_atomic() -> None:
    """The same message claimed from two sessions: exactly one claim wins."""
    message_id = await queued_message()
    factory = get_session_factory()

    async with factory() as first, factory() as second:
        assert await SendRepository(first).claim(message_id, now=NOW) is True
        await first.commit()
        assert await SendRepository(second).claim(message_id, now=NOW) is False
        await second.commit()

    assert (await get_message(message_id)).status == "sending"


async def test_lost_claim_moves_on_to_the_next_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    taken = await queued_message(status="sending", updated_at=NOW)  # another worker's message
    free = await queued_message()
    original = SendRepository.next_candidate
    calls: list[int] = []

    async def racy(self: SendRepository, **kwargs: Any) -> int | None:
        calls.append(1)
        if len(calls) == 1:
            return taken  # the candidate query saw it as queued a moment ago
        return await original(self, **kwargs)

    monkeypatch.setattr(SendRepository, "next_candidate", racy)
    fake = FakeProvider()

    outcome = await send_next(
        get_session_factory(), fake, now=NOW, sleep=Sleeper(), jitter=Jitter()
    )

    assert outcome == "sent"
    assert [sent.client_ref for sent in fake.sent] == [f"msg-{free}"]
    assert (await get_message(taken)).status == "sending"  # not touched by this worker
    assert (await get_message(taken)).attempts == 0


async def test_claim_that_is_always_lost_ends_the_step_as_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = await queued_message(status="sending", updated_at=NOW)
    calls: list[int] = []

    async def always_taken(self: SendRepository, **kwargs: Any) -> int | None:
        calls.append(1)
        return taken

    monkeypatch.setattr(SendRepository, "next_candidate", always_taken)
    fake = FakeProvider()

    outcome = await send_next(
        get_session_factory(), fake, now=NOW, sleep=Sleeper(), jitter=Jitter()
    )

    assert outcome == "idle"
    assert len(calls) == 5  # gave up after the bounded number of tries
    assert fake.sent == []


async def test_second_automatic_message_claimed_in_a_race_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student_id = await add_student()
    first_invoice = await add_invoice(student_id, due=date(2026, 11, 10))
    second_invoice = await add_invoice(student_id, due=date(2026, 11, 12))
    await add_message(first_invoice, student_id, status="sent", sent_at=NOW - timedelta(hours=1))
    waiting = await add_message(second_invoice, student_id)

    async def stale_view(self: SendRepository, **kwargs: Any) -> int | None:
        return waiting  # as if the first message was sent just after the candidate query ran

    monkeypatch.setattr(SendRepository, "next_candidate", stale_view)
    fake = FakeProvider()

    outcome = await send_next(
        get_session_factory(), fake, now=NOW, sleep=Sleeper(), jitter=Jitter()
    )

    assert outcome == "deferred"
    assert fake.sent == []
    message = await get_message(waiting)
    assert message.status == "queued"
    assert message.attempts == 0
    assert message.cancel_reason is None


async def test_concurrent_send_next_calls_never_send_a_message_twice(tmp_path: Path) -> None:
    """Four callers on a file-backed SQLite database (the in-memory test engine shares one
    connection, which cannot show real concurrency): every message goes out exactly once.

    This relies on timing, so the deterministic claim tests above are the real proof.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        await configure(factory, auto_send_enabled=True)
        first = await queued_message(factory=factory)
        second = await queued_message(factory=factory)
        fake = FakeProvider()

        outcomes = await asyncio.gather(
            *(send_next(factory, fake, now=NOW, sleep=Sleeper(), jitter=Jitter()) for _ in range(4))
        )

        assert sorted(outcomes) == ["idle", "idle", "sent", "sent"]
        assert sorted(sent.client_ref for sent in fake.sent) == sorted(
            [f"msg-{first}", f"msg-{second}"]
        )
        async with factory() as db:
            statuses = (await db.scalars(select(Message.status))).all()
            attempts = (await db.scalars(select(Message.attempts))).all()
        assert list(statuses) == ["sent", "sent"]
        assert list(attempts) == [1, 1]
    finally:
        await engine.dispose()
