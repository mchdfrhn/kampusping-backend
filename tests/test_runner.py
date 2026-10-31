import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import cli
from app.db.base import Base
from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages import runner
from app.modules.messages.model import Message
from app.modules.messages.planner import PlanReport
from app.modules.messages.runner import run_worker
from app.modules.schedules.model import ScheduleRule
from app.modules.settings.service import SettingsService
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate
from app.modules.whatsapp.fake import FakeProvider

# Monday 16 November 2026, 10:00 in Jakarta (UTC+7): inside the default send window.
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
TODAY = date(2026, 11, 16)
TICK = 60
THROTTLE = 0.5

_numbers = iter(range(1, 10_000))


class FixedClock:
    def __call__(self) -> datetime:
        return NOW


class RecordingSleep:
    """Records every pause; raises ``CancelledError`` on the ``stop_after``-th tick pause."""

    def __init__(self, stop_after: int | None = None) -> None:
        self.calls: list[float] = []
        self._stop_after = stop_after

    @property
    def tick_pauses(self) -> list[float]:
        return [seconds for seconds in self.calls if seconds == TICK]

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        stop = self._stop_after
        if seconds == TICK and stop is not None and len(self.tick_pauses) >= stop:
            raise asyncio.CancelledError


def jitter(low: int, high: int) -> float:
    return THROTTLE


@pytest.fixture(autouse=True)
async def sending_enabled(database: None) -> None:
    async with get_session_factory()() as db:
        row = await SettingsService(db).get()
        row.auto_send_enabled = True
        await db.commit()


async def add_student_with_invoice(*, due: date) -> tuple[int, int]:
    number = next(_numbers)
    async with get_session_factory()() as db:
        student = Student(
            nim=f"2021{number:04d}",
            name="Budi Santoso",
            phone=f"+628123456{number:04d}",
            phone_status="valid",
        )
        db.add(student)
        await db.flush()
        invoice = Invoice(
            invoice_code=f"INV-{number}",
            student_id=student.id,
            semester="Ganjil 2026/2027",
            amount=1_250_000,
            due_date=due,
            status="unpaid",
            source="siakad",
        )
        db.add(invoice)
        await db.commit()
        return student.id, invoice.id


async def add_rule(offset: int = 0) -> None:
    async with get_session_factory()() as db:
        template = MessageTemplate(
            name="Templat", body="Halo {{nama}}, tagihan Rp{{nominal}}.", is_active=True
        )
        db.add(template)
        await db.flush()
        db.add(
            ScheduleRule(name="Aturan", offset_days=offset, template_id=template.id, is_active=True)
        )
        await db.commit()


async def add_manual_message(student_id: int, invoice_id: int) -> int:
    async with get_session_factory()() as db:
        message = Message(
            student_id=student_id,
            invoice_id=invoice_id,
            kind="manual",
            scheduled_date=TODAY,
            body="Halo, ini pesan manual.",
            status="queued",
            queued_at=NOW - timedelta(days=1),
        )
        db.add(message)
        await db.commit()
        return message.id


async def statuses() -> list[str]:
    async with get_session_factory()() as db:
        rows = await db.execute(Message.__table__.select().order_by(Message.id))
        return [row.status for row in rows]


def worker(provider: FakeProvider, sleep: RecordingSleep, *, once: bool = False) -> Any:
    return run_worker(
        session_factory=get_session_factory(),
        provider=provider,
        clock=FixedClock(),
        sleep=sleep,
        jitter=jitter,
        tick_seconds=TICK,
        once=once,
    )


async def test_once_runs_plan_then_send() -> None:
    await add_student_with_invoice(due=TODAY)
    await add_rule(offset=0)
    provider = FakeProvider()

    await worker(provider, RecordingSleep(), once=True)

    (sent,) = provider.sent
    assert sent.text.startswith("Halo Budi Santoso")
    assert await statuses() == ["sent"]


async def test_exception_in_plan_does_not_kill_the_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Student A has a manual message queued (no rule can plan it); student B is only planned on
    # the second tick. The first tick's planning raises but its send phase must still run.
    student_a, invoice_a = await add_student_with_invoice(due=date(2026, 10, 1))
    await add_manual_message(student_a, invoice_a)
    await add_student_with_invoice(due=TODAY)
    await add_rule(offset=0)

    original = runner.plan_messages
    provider = FakeProvider()
    sent_when_planning: list[int] = []  # messages already sent at the start of each plan call

    async def flaky(db: AsyncSession, *, now: datetime) -> PlanReport:
        sent_when_planning.append(len(provider.sent))
        if len(sent_when_planning) == 1:
            raise RuntimeError("database hiccup")
        return await original(db, now=now)

    monkeypatch.setattr(runner, "plan_messages", flaky)
    sleep = RecordingSleep(stop_after=2)
    caplog.set_level(logging.INFO, logger="app.modules.messages.runner")

    with pytest.raises(asyncio.CancelledError):
        await worker(provider, sleep)

    # Tick 2 starts with A already sent: tick 1's send phase ran although its planning failed.
    assert sent_when_planning == [0, 1]
    assert len(provider.sent) == 2  # A in tick 1, B (planned by tick 2) in tick 2
    assert any(record.levelno == logging.ERROR and record.exc_info for record in caplog.records), (
        "the planning failure is logged with its traceback"
    )


async def test_stops_on_cancellation() -> None:
    sleeping = asyncio.Event()
    never = asyncio.Event()

    async def blocking_sleep(seconds: float) -> None:
        if seconds == TICK:
            sleeping.set()
            await never.wait()

    task = asyncio.create_task(
        run_worker(
            session_factory=get_session_factory(),
            provider=FakeProvider(),
            clock=FixedClock(),
            sleep=blocking_sleep,
            jitter=jitter,
            tick_seconds=TICK,
        )
    )
    await asyncio.wait_for(sleeping.wait(), timeout=10)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert task.cancelled()


async def test_cancellation_inside_a_tick_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancelled(db: AsyncSession, *, now: datetime) -> PlanReport:
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "plan_messages", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await worker(FakeProvider(), RecordingSleep())


async def test_keyboard_interrupt_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(db: AsyncSession, *, now: datetime) -> PlanReport:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "plan_messages", interrupted)

    with pytest.raises(KeyboardInterrupt):
        await worker(FakeProvider(), RecordingSleep())


async def test_once_does_not_sleep() -> None:
    sleep = RecordingSleep()

    await worker(FakeProvider(), sleep, once=True)

    assert sleep.calls == []


async def test_sleeps_tick_seconds_between_ticks() -> None:
    sleep = RecordingSleep(stop_after=3)

    with pytest.raises(asyncio.CancelledError):
        await worker(FakeProvider(), sleep)

    assert sleep.tick_pauses == [TICK, TICK, TICK]


async def test_each_tick_logs_counts_without_personal_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await add_student_with_invoice(due=TODAY)
    await add_rule(offset=0)
    caplog.set_level(logging.INFO, logger="app.modules.messages.runner")

    await worker(FakeProvider(), RecordingSleep(), once=True)

    lines = [r.getMessage() for r in caplog.records if r.name == "app.modules.messages.runner"]
    assert len(lines) == 1
    assert "created=1" in lines[0]
    assert "handled=1" in lines[0]
    assert "Budi" not in lines[0]
    assert "+62" not in lines[0]


# --- CLI -----------------------------------------------------------------------------------


@pytest.fixture
async def file_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """The CLI pointed at an empty file-backed SQLite database with the fake provider."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(cli, "get_session_factory", lambda: factory)
    monkeypatch.setattr(cli, "get_engine", lambda: engine)
    monkeypatch.setattr(cli.settings, "wa_provider", "fake")
    yield
    await engine.dispose()


async def broken_plan(db: AsyncSession, *, now: datetime) -> PlanReport:
    raise RuntimeError("boom")


async def broken_send_phase(*args: Any, **kwargs: Any) -> int:
    raise RuntimeError("boom")


@pytest.mark.usefixtures("file_database")
async def test_run_worker_cli_once_with_fake_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await cli._run(argparse.Namespace(command="run-worker", once=True))  # exit status 0

    out = capsys.readouterr().out.strip()
    assert out == "Worker tick done: created=0 expired=0 superseded=0 escalated=0 handled=0"


@pytest.mark.usefixtures("file_database")
async def test_run_worker_command_is_healthy_with_exit_code_0() -> None:
    summary, code = await cli.run_worker_command(once=True)

    assert code == 0
    assert summary.startswith("Worker tick done: created=0")


@pytest.mark.usefixtures("file_database")
async def test_run_worker_command_exits_1_when_planning_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "plan_messages", broken_plan)

    summary, code = await cli.run_worker_command(once=True)

    assert code == 1
    assert summary == "Worker tick done: planning failed (see the log) handled=0"


@pytest.mark.usefixtures("file_database")
async def test_run_worker_command_exits_1_when_the_send_phase_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "run_send_phase", broken_send_phase)

    summary, code = await cli.run_worker_command(once=True)

    assert code == 1
    assert summary == (
        "Worker tick done: created=0 expired=0 superseded=0 escalated=0 handled=0"
        " send phase failed (see the log)"
    )


@pytest.mark.usefixtures("file_database")
@pytest.mark.parametrize("broken", ["plan", "send"])
async def test_run_worker_cli_once_exits_non_zero_on_a_failed_tick(
    broken: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if broken == "plan":
        monkeypatch.setattr(runner, "plan_messages", broken_plan)
    else:
        monkeypatch.setattr(runner, "run_send_phase", broken_send_phase)

    with pytest.raises(SystemExit) as exit_info:
        await cli._run(argparse.Namespace(command="run-worker", once=True))

    assert exit_info.value.code == 1
    assert "Worker tick done:" in capsys.readouterr().out


async def test_send_phase_exception_does_not_kill_the_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    async def flaky_send_phase(*args: Any, **kwargs: Any) -> int:
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "run_send_phase", flaky_send_phase)
    sleep = RecordingSleep(stop_after=2)

    with pytest.raises(asyncio.CancelledError):
        await worker(FakeProvider(), sleep)

    assert len(calls) == 2
    assert sleep.tick_pauses == [TICK, TICK]


@pytest.mark.usefixtures("file_database")
async def test_provider_cache_is_reset_even_if_aclose_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadClose(FakeProvider):
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    resets: list[int] = []
    monkeypatch.setattr(cli, "get_provider", lambda: BadClose())
    monkeypatch.setattr(cli, "reset_provider_cache", lambda: resets.append(1))

    with pytest.raises(RuntimeError, match="close failed"):
        await cli.run_worker_command(once=True)

    assert resets == [1]
