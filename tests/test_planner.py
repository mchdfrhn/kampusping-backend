from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.exc import IntegrityError

from app.db.session import get_engine, get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages.constants import CancelReason, MessageKind, MessageStatus
from app.modules.messages.model import Message
from app.modules.messages.planner import PlanReport, plan_messages
from app.modules.messages.repository import MessageRepository
from app.modules.schedules.model import ScheduleRule
from app.modules.settings.model import AppSettings
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate
from app.modules.templates.rendering import OPT_OUT_FOOTER, build_message_body
from app.shared.utils import ensure_utc

# Sunday 15 November 2026, 10:00 in Jakarta (UTC+7).
NOW = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
TODAY = date(2026, 11, 15)
TEMPLATE_BODY = "Halo {{nama}}, tagihan Rp{{nominal}} jatuh tempo {{tanggal_jatuh_tempo}}."


async def add_student(
    *,
    nim: str = "2021001",
    name: str = "Budi Santoso",
    phone_status: str = "valid",
    opted_out: bool = False,
    deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        student = Student(
            nim=nim,
            name=name,
            phone="6281234567890",
            phone_status=phone_status,
            opted_out_at=NOW if opted_out else None,
            deleted_at=NOW if deleted else None,
        )
        db.add(student)
        await db.commit()
        return student.id


async def add_invoice(
    student_id: int,
    *,
    due: date,
    code: str = "INV-1",
    status: str = "unpaid",
    exempt_reason: str | None = None,
    exempt_until: date | None = None,
    escalated_at: datetime | None = None,
    deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        invoice = Invoice(
            invoice_code=code,
            student_id=student_id,
            semester="Ganjil 2026/2027",
            amount=1_250_000,
            due_date=due,
            status=status,
            source="siakad",
            exempt_reason=exempt_reason,
            exempt_until=exempt_until,
            escalated_at=escalated_at,
            deleted_at=NOW if deleted else None,
        )
        db.add(invoice)
        await db.commit()
        return invoice.id


async def add_rule(
    offset: int,
    *,
    body: str = TEMPLATE_BODY,
    rule_active: bool = True,
    template_active: bool = True,
    rule_deleted: bool = False,
    template_deleted: bool = False,
) -> int:
    async with get_session_factory()() as db:
        template = MessageTemplate(
            name=f"Templat {offset}",
            body=body,
            is_active=template_active,
            deleted_at=NOW if template_deleted else None,
        )
        db.add(template)
        await db.flush()
        rule = ScheduleRule(
            name=f"Aturan {offset}",
            offset_days=offset,
            template_id=template.id,
            is_active=rule_active,
            deleted_at=NOW if rule_deleted else None,
        )
        db.add(rule)
        await db.commit()
        return rule.id


async def add_message(
    invoice_id: int,
    student_id: int,
    *,
    scheduled: date,
    rule_id: int | None = None,
    kind: str = "auto",
    status: str = "queued",
) -> int:
    async with get_session_factory()() as db:
        message = Message(
            student_id=student_id,
            invoice_id=invoice_id,
            rule_id=rule_id,
            kind=kind,
            scheduled_date=scheduled,
            body="x",
            status=status,
            queued_at=NOW,
        )
        db.add(message)
        await db.commit()
        return message.id


async def run_planner(now: datetime = NOW) -> PlanReport:
    async with get_session_factory()() as db:
        return await plan_messages(db, now=now)


async def all_messages() -> list[Message]:
    async with get_session_factory()() as db:
        return list((await db.scalars(select(Message).order_by(Message.id))).all())


async def get_invoice(invoice_id: int) -> Invoice:
    async with get_session_factory()() as db:
        invoice = await db.get(Invoice, invoice_id)
        assert invoice is not None
        return invoice


async def plain_setup(*, offset: int = 0, due: date = TODAY) -> tuple[int, int, int]:
    """One student, one unpaid invoice and one active rule. Returns their ids."""
    student_id = await add_student()
    invoice_id = await add_invoice(student_id, due=due)
    rule_id = await add_rule(offset)
    return student_id, invoice_id, rule_id


# --- Planning window -----------------------------------------------------------------------


async def test_creates_message_when_rule_date_is_today() -> None:
    student_id, invoice_id, rule_id = await plain_setup(offset=-5, due=date(2026, 11, 20))

    report = await run_planner()

    assert report == PlanReport(created=1, expired=0, superseded=0, escalated=0)
    (message,) = await all_messages()
    assert message.student_id == student_id
    assert message.invoice_id == invoice_id
    assert message.rule_id == rule_id
    assert message.kind == MessageKind.AUTO.value
    assert message.status == MessageStatus.QUEUED.value
    assert message.scheduled_date == TODAY  # due_date + offset_days
    assert message.attempts == 0
    assert message.cancel_reason is None
    assert ensure_utc(message.queued_at) == NOW
    assert message.created_by == "system"
    assert message.updated_by == "system"


async def test_scheduled_date_equals_due_date_plus_offset() -> None:
    await plain_setup(offset=3, due=date(2026, 11, 12))  # 12 + 3 = 15

    await run_planner()

    (message,) = await all_messages()
    assert message.scheduled_date == date(2026, 11, 15)


async def test_rule_date_in_past_3_days_is_still_planned() -> None:
    await plain_setup(offset=0, due=date(2026, 11, 13))  # a Friday
    monday = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)

    report = await run_planner(monday)

    assert report.created == 1
    (message,) = await all_messages()
    assert message.scheduled_date == date(2026, 11, 13)


async def test_rule_date_older_than_3_days_not_planned() -> None:
    await plain_setup(offset=0, due=date(2026, 11, 12))
    monday = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)

    report = await run_planner(monday)

    assert report.created == 0
    assert await all_messages() == []


async def test_rule_date_in_the_future_is_not_planned() -> None:
    await plain_setup(offset=0, due=date(2026, 11, 16))

    assert (await run_planner()).created == 0


async def test_planning_twice_is_idempotent() -> None:
    await plain_setup()

    first = await run_planner()
    second = await run_planner()

    assert first.created == 1
    assert second == PlanReport(created=0, expired=0, superseded=0, escalated=0)
    assert len(await all_messages()) == 1


async def test_cancelled_message_is_not_planned_again() -> None:
    _student_id, invoice_id, _rule_id = await plain_setup()
    await run_planner()
    async with get_session_factory()() as db:
        message = (await db.scalars(select(Message))).one()
        message.status = MessageStatus.CANCELLED.value
        message.cancel_reason = CancelReason.MANUAL.value
        await db.commit()

    assert (await run_planner()).created == 0
    assert [m.invoice_id for m in await all_messages()] == [invoice_id]


async def test_midnight_boundary_uses_jakarta_date() -> None:
    await plain_setup(offset=0, due=date(2026, 11, 15))

    just_before = await run_planner(datetime(2026, 11, 14, 16, 59, tzinfo=UTC))  # 23:59 on the 14th
    assert just_before.created == 0

    just_after = await run_planner(datetime(2026, 11, 14, 17, 30, tzinfo=UTC))  # 00:30 on the 15th
    assert just_after.created == 1
    (message,) = await all_messages()
    assert message.scheduled_date == date(2026, 11, 15)


# --- Who is skipped ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason", ["paid", "opted_out", "exempt", "exempt_forever", "phone_invalid", "phone_missing"]
)
async def test_skips_paid_opted_out_exempt_and_invalid_phone(reason: str) -> None:
    student_id = await add_student(
        phone_status={"phone_invalid": "invalid", "phone_missing": "missing"}.get(reason, "valid"),
        opted_out=reason == "opted_out",
    )
    await add_invoice(
        student_id,
        due=TODAY,
        status="paid" if reason == "paid" else "unpaid",
        exempt_reason="Beasiswa" if reason in ("exempt", "exempt_forever") else None,
        exempt_until=TODAY + timedelta(days=30) if reason == "exempt" else None,
    )
    await add_rule(0)

    report = await run_planner()

    assert report.created == 0
    assert await all_messages() == []


async def test_exempt_until_today_is_still_exempt() -> None:
    student_id = await add_student()
    await add_invoice(student_id, due=TODAY, exempt_reason="Cicilan", exempt_until=TODAY)
    await add_rule(0)

    assert (await run_planner()).created == 0


async def test_exempt_with_expired_until_is_planned() -> None:
    student_id = await add_student()
    await add_invoice(
        student_id, due=TODAY, exempt_reason="Cicilan", exempt_until=TODAY - timedelta(days=1)
    )
    await add_rule(0)

    assert (await run_planner()).created == 1


@pytest.mark.parametrize("target", ["student", "invoice"])
async def test_soft_deleted_student_or_invoice_is_skipped(target: str) -> None:
    student_id = await add_student(deleted=target == "student")
    await add_invoice(student_id, due=TODAY, deleted=target == "invoice")
    await add_rule(0)

    assert (await run_planner()).created == 0


@pytest.mark.parametrize("flaw", ["inactive_rule", "inactive_template", "deleted_rule"])
async def test_inactive_or_deleted_rule_and_inactive_template_are_skipped(flaw: str) -> None:
    student_id = await add_student()
    await add_invoice(student_id, due=TODAY)
    await add_rule(
        0,
        rule_active=flaw != "inactive_rule",
        template_active=flaw != "inactive_template",
        rule_deleted=flaw == "deleted_rule",
    )

    assert (await run_planner()).created == 0


async def test_deleted_template_is_skipped() -> None:
    student_id = await add_student()
    await add_invoice(student_id, due=TODAY)
    await add_rule(0, template_deleted=True)

    assert (await run_planner()).created == 0


# --- Expiry, supersede, escalation ---------------------------------------------------------


async def test_queued_older_than_3_days_become_cancelled_expired() -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(student_id, due=date(2026, 12, 31))
    stale = await add_message(invoice_id, student_id, scheduled=date(2026, 11, 11))
    other_invoice = await add_invoice(student_id, due=date(2026, 12, 31), code="INV-2")
    edge = await add_message(other_invoice, student_id, scheduled=date(2026, 11, 12))
    manual_invoice = await add_invoice(student_id, due=date(2026, 12, 31), code="INV-3")
    manual = await add_message(
        manual_invoice, student_id, scheduled=date(2026, 11, 1), kind=MessageKind.MANUAL.value
    )

    report = await run_planner()

    assert report.expired == 1
    by_id = {m.id: m for m in await all_messages()}
    assert by_id[stale].status == MessageStatus.CANCELLED.value
    assert by_id[stale].cancel_reason == CancelReason.EXPIRED.value
    assert by_id[edge].status == MessageStatus.QUEUED.value  # exactly 3 days old still counts
    assert by_id[manual].status == MessageStatus.QUEUED.value  # manual messages never expire


async def test_only_latest_rule_survives_per_invoice() -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(student_id, due=TODAY)
    older_rule = await add_rule(-2)
    await add_rule(0)
    older = await add_message(
        invoice_id, student_id, scheduled=date(2026, 11, 13), rule_id=older_rule
    )

    report = await run_planner()

    assert report.created == 1  # the rule dated today; the older rule already has its message
    assert report.superseded == 1
    messages = {m.id: m for m in await all_messages()}
    assert messages[older].status == MessageStatus.CANCELLED.value
    assert messages[older].cancel_reason == CancelReason.SUPERSEDED.value
    survivors = [m for m in messages.values() if m.status == MessageStatus.QUEUED.value]
    assert [m.scheduled_date for m in survivors] == [TODAY]


async def test_two_rules_in_window_are_both_planned_then_the_later_survives() -> None:
    student_id = await add_student()
    await add_invoice(student_id, due=TODAY)
    early_rule = await add_rule(-3)  # rule date 12 November
    late_rule = await add_rule(0)  # rule date 15 November

    report = await run_planner()

    assert report.created == 2
    assert report.superseded == 1
    by_rule = {m.rule_id: m for m in await all_messages()}
    assert by_rule[early_rule].status == MessageStatus.CANCELLED.value
    assert by_rule[early_rule].cancel_reason == CancelReason.SUPERSEDED.value
    assert by_rule[late_rule].status == MessageStatus.QUEUED.value

    again = await run_planner()
    assert again == PlanReport(created=0, expired=0, superseded=0, escalated=0)


async def test_supersede_tie_keeps_the_highest_id() -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(student_id, due=TODAY)
    first = await add_message(invoice_id, student_id, scheduled=TODAY)
    second = await add_message(invoice_id, student_id, scheduled=TODAY)
    third = await add_message(invoice_id, student_id, scheduled=TODAY)

    report = await run_planner()

    assert report.superseded == 2
    by_id = {m.id: m.status for m in await all_messages()}
    assert by_id == {
        first: MessageStatus.CANCELLED.value,
        second: MessageStatus.CANCELLED.value,
        third: MessageStatus.QUEUED.value,
    }


async def test_supersede_leaves_sent_messages_alone() -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(student_id, due=TODAY)
    sent = await add_message(
        invoice_id, student_id, scheduled=date(2026, 11, 13), status=MessageStatus.SENT.value
    )
    queued = await add_message(invoice_id, student_id, scheduled=TODAY)

    report = await run_planner()

    assert report.superseded == 0
    by_id = {m.id: m.status for m in await all_messages()}
    assert by_id == {sent: MessageStatus.SENT.value, queued: MessageStatus.QUEUED.value}


async def test_invoice_past_escalation_offset_gets_escalated_at_and_no_message() -> None:
    student_id = await add_student()
    # Default escalation offset is 14 days: due 25 October escalates on 8 November.
    invoice_id = await add_invoice(student_id, due=date(2026, 10, 25))
    queued = await add_message(invoice_id, student_id, scheduled=date(2026, 11, 14))
    await add_rule(20)  # rule date 14 November: in the window, so it would be planned otherwise

    report = await run_planner()

    assert report.escalated == 1
    assert report.created == 0
    invoice = await get_invoice(invoice_id)
    assert invoice.escalated_at is not None
    assert ensure_utc(invoice.escalated_at) == NOW
    (message,) = await all_messages()
    assert message.id == queued
    assert message.status == MessageStatus.CANCELLED.value
    assert message.cancel_reason == CancelReason.ESCALATED.value

    again = await run_planner()
    assert again == PlanReport(created=0, expired=0, superseded=0, escalated=0)


async def test_escalation_boundary_is_inclusive() -> None:
    student_id = await add_student()
    on_boundary = await add_invoice(student_id, due=date(2026, 11, 1), code="A")  # exactly 14 days
    one_day_short = await add_invoice(student_id, due=date(2026, 11, 2), code="B")

    report = await run_planner()

    assert report.escalated == 1
    assert (await get_invoice(on_boundary)).escalated_at is not None
    assert (await get_invoice(one_day_short)).escalated_at is None


@pytest.mark.parametrize("kind", ["paid", "exempt", "deleted"])
async def test_paid_exempt_or_deleted_invoices_are_not_escalated(kind: str) -> None:
    student_id = await add_student()
    invoice_id = await add_invoice(
        student_id,
        due=date(2026, 10, 1),
        status="paid" if kind == "paid" else "unpaid",
        exempt_reason="Beasiswa" if kind == "exempt" else None,
        deleted=kind == "deleted",
    )

    report = await run_planner()

    assert report.escalated == 0
    assert (await get_invoice(invoice_id)).escalated_at is None


async def test_escalated_invoice_is_never_planned() -> None:
    student_id = await add_student()
    await add_invoice(student_id, due=TODAY, escalated_at=NOW - timedelta(days=1))
    await add_rule(0)

    assert (await run_planner()).created == 0


# --- Body ----------------------------------------------------------------------------------


async def test_message_body_is_rendered_at_planning_time_with_footer() -> None:
    async with get_session_factory()() as db:
        db.add(AppSettings(id=1, campus_name="Kampus Contoh"))
        await db.commit()
    _student_id, invoice_id, rule_id = await plain_setup(offset=0, due=date(2026, 11, 15))

    await run_planner()

    (message,) = await all_messages()
    assert message.body == (
        f"Halo Budi Santoso, tagihan Rp1.250.000 jatuh tempo 15 November 2026.\n\n{OPT_OUT_FOOTER}"
    )
    async with get_session_factory()() as db:
        invoice = await db.get(Invoice, invoice_id)
        rule = await db.get(ScheduleRule, rule_id)
        assert invoice is not None
        assert rule is not None
        expected = build_message_body(
            rule.template,
            student=invoice.student,
            invoice=invoice,
            settings=(await db.scalars(select(AppSettings))).one(),
        )
        assert message.body == expected

        # Editing the student or template later does not change the queued text.
        invoice.student.name = "Nama Baru"
        rule.template.body = "Isi baru"
        await db.commit()
    (after,) = await all_messages()
    assert after.body == message.body


# --- Model and repository ------------------------------------------------------------------


async def test_manual_messages_with_null_rule_may_repeat_but_a_rule_pair_may_not() -> None:
    student_id, invoice_id, rule_id = await plain_setup()
    manual = MessageKind.MANUAL.value
    await add_message(invoice_id, student_id, scheduled=TODAY, kind=manual)
    await add_message(invoice_id, student_id, scheduled=TODAY, kind=manual)  # NULL rule: allowed
    await add_message(invoice_id, student_id, scheduled=TODAY, rule_id=rule_id)

    with pytest.raises(IntegrityError):
        await add_message(invoice_id, student_id, scheduled=TODAY, rule_id=rule_id)
    assert len(await all_messages()) == 3


async def test_insert_messages_skips_duplicates_and_keeps_the_rest() -> None:
    student_id, invoice_id, rule_id = await plain_setup()
    other_invoice = await add_invoice(student_id, due=TODAY, code="INV-2")
    await add_message(invoice_id, student_id, scheduled=TODAY, rule_id=rule_id)

    def make(invoice: int) -> dict[str, Any]:
        return {
            "student_id": student_id,
            "invoice_id": invoice,
            "rule_id": rule_id,
            "kind": "auto",
            "scheduled_date": TODAY,
            "body": "x",
            "status": "queued",
            "queued_at": NOW,
        }

    async with get_session_factory()() as db:
        inserted = await MessageRepository(db).insert_messages(
            [make(invoice_id), make(other_invoice)]
        )
        await db.commit()

    assert inserted == 1
    assert sorted(m.invoice_id for m in await all_messages()) == sorted([invoice_id, other_invoice])


async def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await run_planner(datetime(2026, 11, 15, 3, 0))  # naive on purpose


# --- Scale ---------------------------------------------------------------------------------


async def test_planning_5000_invoices_uses_a_handful_of_statements() -> None:
    count = 5000
    async with get_session_factory()() as db:
        await db.execute(
            insert(Student),
            [
                {
                    "nim": f"N{i:05d}",
                    "name": f"Mahasiswa {i}",
                    "phone": "6281234567890",
                    "phone_status": "valid",
                }
                for i in range(count)
            ],
        )
        student_ids = list((await db.scalars(select(Student.id).order_by(Student.id))).all())
        await db.execute(
            insert(Invoice),
            [
                {
                    "invoice_code": f"INV-{i:05d}",
                    "student_id": student_ids[i],
                    "semester": "Ganjil 2026/2027",
                    "amount": 1_000_000,
                    "due_date": TODAY,
                    "status": "unpaid",
                    "source": "siakad",
                }
                for i in range(count)
            ],
        )
        await db.commit()
    await add_rule(0)

    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        report = await run_planner()
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)

    assert report.created == count
    assert len(statements) <= 20, f"{len(statements)} statements for {count} invoices"
