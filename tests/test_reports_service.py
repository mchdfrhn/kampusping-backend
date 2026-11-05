"""ReportService: invoice figures, groups and the arrears list (FR-14)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.exceptions import AppException
from app.db.session import get_session_factory
from app.modules.reports.constants import ReportPreset
from app.modules.reports.schema import ReportData
from app.modules.reports.service import ReportService

from .reports_helpers import NOW, TODAY, add_invoice, add_message, add_student, at

SEM_A = "Ganjil 2026/2027"
SEM_B = "Genap 2026/2027"
ACTOR = "staf@example.com"


async def build(
    preset: ReportPreset = ReportPreset.WEEKLY,
    semester: str | None = None,
    *,
    now: datetime = NOW,
) -> ReportData:
    async with get_session_factory()() as db:
        return await ReportService(db, lambda: now).build(preset, semester, ACTOR)


async def test_summary_counts_and_amounts_per_status() -> None:
    student = await add_student("2201")
    await add_invoice(student, "P1", amount=1_000_000, status="paid", paid_at=TODAY - timedelta(1))
    await add_invoice(student, "O1", amount=2_000_000, due=TODAY - timedelta(days=5))
    await add_invoice(student, "F1", amount=3_000_000, due=TODAY + timedelta(days=5))

    data = await build()

    invoices = data.summary.invoices
    assert (invoices.total, invoices.total_amount) == (3, 6_000_000)
    assert (invoices.paid, invoices.paid_amount) == (1, 1_000_000)
    assert (invoices.unpaid, invoices.unpaid_amount) == (2, 5_000_000)
    assert (invoices.overdue, invoices.overdue_amount) == (1, 2_000_000)
    assert (invoices.exempt, invoices.exempt_amount) == (0, 0)
    assert invoices.paid_percent_count == 33.3
    assert invoices.paid_percent_amount == 16.7
    assert data.generated_at == NOW
    assert data.generated_by == ACTOR
    assert data.preset is ReportPreset.WEEKLY


async def test_overdue_excludes_paid_due_today_exempt_and_deleted() -> None:
    student = await add_student("2201")
    gone_student = await add_student("2202", deleted=True)
    await add_invoice(student, "TODAY", due=TODAY)
    await add_invoice(student, "PAID", due=TODAY - timedelta(10), status="paid", paid_at=TODAY)
    await add_invoice(student, "EXEMPT", due=TODAY - timedelta(1), exempt_reason="Cicilan")
    await add_invoice(student, "REAL", amount=2_500_000, due=TODAY - timedelta(2))
    await add_invoice(student, "GONE", due=TODAY - timedelta(3), deleted=True)
    await add_invoice(gone_student, "GONE-STUDENT", due=TODAY - timedelta(3))

    invoices = (await build()).summary.invoices

    assert invoices.total == 4  # soft-deleted invoice and the deleted student's invoice are out
    assert (invoices.overdue, invoices.overdue_amount) == (1, 2_500_000)
    assert (invoices.exempt, invoices.exempt_amount) == (1, 1_000_000)
    assert invoices.unpaid == 3  # exempt invoices are still unpaid


async def test_exemption_follows_its_end_date() -> None:
    student = await add_student("2201")
    await add_invoice(
        student, "ACTIVE", due=TODAY - timedelta(5), exempt_reason="x", exempt_until=TODAY
    )
    await add_invoice(
        student,
        "EXPIRED",
        due=TODAY - timedelta(5),
        exempt_reason="x",
        exempt_until=TODAY - timedelta(1),
    )

    data = await build()

    assert (data.summary.invoices.exempt, data.summary.invoices.overdue) == (1, 1)
    assert [row.invoice_code for row in data.arrears] == ["EXPIRED"]


async def test_semester_preset_restricts_to_the_semester_and_has_no_period() -> None:
    student = await add_student("2201")
    await add_invoice(student, "A1", semester=SEM_A, due=TODAY - timedelta(1))
    await add_invoice(student, "B1", semester=SEM_B, due=TODAY - timedelta(1))
    await add_invoice(student, "B2", semester=SEM_B, status="paid", paid_at=TODAY)

    data = await build(ReportPreset.SEMESTER, SEM_B)

    assert data.semester == SEM_B
    assert (data.period_start, data.period_end) == (None, None)
    assert data.summary.payments_in_period is None
    assert data.summary.invoices.total == 2
    assert [row.invoice_code for row in data.arrears] == ["B1"]


async def test_weekly_covers_every_semester_and_ignores_the_semester_argument() -> None:
    student = await add_student("2201")
    await add_invoice(student, "A1", semester=SEM_A, due=TODAY - timedelta(1))
    await add_invoice(student, "B1", semester=SEM_B, due=TODAY - timedelta(1))

    data = await build(ReportPreset.WEEKLY, SEM_A)

    assert data.semester is None
    assert data.summary.invoices.total == 2


async def test_weekly_window_edges() -> None:
    student = await add_student("2201")
    for code, paid_at, amount in (
        ("OUT-BEFORE", date(2026, 10, 26), 100_000),
        ("IN-FIRST", date(2026, 10, 27), 200_000),
        ("IN-LAST", date(2026, 11, 2), 400_000),
        ("OUT-AFTER", date(2026, 11, 3), 800_000),
    ):
        await add_invoice(student, code, status="paid", paid_at=paid_at, amount=amount)

    data = await build()

    assert (data.period_start, data.period_end) == (date(2026, 10, 27), date(2026, 11, 2))
    payments = data.summary.payments_in_period
    assert payments is not None
    assert (payments.count, payments.amount) == (2, 600_000)


async def test_weekly_window_follows_jakarta_midnight() -> None:
    student = await add_student("2201")
    await add_invoice(student, "DUE-NOV-2", due=date(2026, 11, 2))
    late_utc_evening = datetime(2026, 11, 2, 17, 30, tzinfo=UTC)  # 00:30 WIB on 3 November

    data = await build(now=late_utc_evening)

    assert (data.period_start, data.period_end) == (date(2026, 10, 28), date(2026, 11, 3))
    assert data.summary.invoices.overdue == 1  # due 2 November is overdue once "today" is the 3rd


async def test_groups_merge_missing_values_and_sort_with_missing_last() -> None:
    ti = await add_student("2201", program="Teknik Informatika", cohort="2022")
    ak = await add_student("2202", program="Akuntansi", cohort="2023")
    none_student = await add_student("2203", program=None, cohort=None)
    blank_student = await add_student("2204", program="", cohort="")
    await add_invoice(ti, "TI-P", amount=1_000_000, status="paid", paid_at=TODAY)
    await add_invoice(ti, "TI-O", amount=2_000_000, due=TODAY - timedelta(1))
    await add_invoice(ak, "AK-F")
    await add_invoice(none_student, "N-O", amount=500_000, due=TODAY - timedelta(1))
    await add_invoice(blank_student, "B-F")

    groups = (await build()).groups

    assert [(g.study_program, g.cohort) for g in groups] == [
        ("Akuntansi", "2023"),
        ("Teknik Informatika", "2022"),
        ("(tanpa prodi)", "(tanpa angkatan)"),
    ]
    ti_row = groups[1]
    assert (ti_row.total, ti_row.paid, ti_row.unpaid, ti_row.overdue) == (2, 1, 1, 1)
    assert (ti_row.overdue_amount, ti_row.paid_percent) == (2_000_000, 50.0)
    missing_row = groups[2]
    assert (missing_row.total, missing_row.overdue, missing_row.overdue_amount) == (2, 1, 500_000)


async def test_arrears_rows_sorted_with_days_overdue_and_labels() -> None:
    first = await add_student("2203", name="Citra", program=None, cohort="2021")
    second = await add_student("2201", name="Ani", program="Akuntansi", cohort="2023")
    await add_invoice(first, "C1", amount=1_500_000, due=TODAY - timedelta(days=10))
    await add_invoice(second, "A1", amount=750_000, due=TODAY - timedelta(days=10))
    await add_invoice(second, "A2", semester=SEM_B, due=TODAY - timedelta(days=3))

    arrears = (await build()).arrears

    assert [(row.nim, row.invoice_code, row.days_overdue) for row in arrears] == [
        ("2201", "A1", 10),
        ("2203", "C1", 10),
        ("2201", "A2", 3),
    ]
    citra = arrears[1]
    assert (citra.name, citra.study_program, citra.cohort) == ("Citra", "(tanpa prodi)", "2021")
    assert (citra.amount, citra.due_date, citra.semester) == (
        1_500_000,
        TODAY - timedelta(10),
        SEM_A,
    )
    assert (citra.messages_sent, citra.last_message_date, citra.last_message_status) == (
        0,
        None,
        None,
    )


async def test_empty_database_yields_zeros_without_division_error() -> None:
    data = await build()

    invoices = data.summary.invoices
    assert (invoices.total, invoices.paid_percent_count, invoices.paid_percent_amount) == (
        0,
        0.0,
        0.0,
    )
    assert data.groups == []
    assert data.arrears == []
    assert data.summary.payments_in_period is not None
    assert data.summary.payments_in_period.count == 0


async def test_semester_preset_requires_a_semester() -> None:
    with pytest.raises(AppException) as raised:
        await build(ReportPreset.SEMESTER, None)

    assert raised.value.status_code == 422


async def test_unknown_semester_yields_a_zero_report() -> None:
    student = await add_student("2201")
    await add_invoice(student, "A1", semester=SEM_A, due=TODAY - timedelta(1))

    data = await build(ReportPreset.SEMESTER, "Tidak Ada")

    assert data.semester == "Tidak Ada"
    assert data.summary.invoices.total == 0
    assert data.arrears == []


async def test_semesters_lists_distinct_non_empty_newest_first() -> None:
    student = await add_student("2201")
    await add_invoice(student, "S1", semester="2025/2026")
    await add_invoice(student, "S2", semester="2026/2027")
    await add_invoice(student, "S3", semester="2026/2027")
    await add_invoice(student, "S4", semester="2024/2025")
    await add_invoice(student, "S5", semester="")
    await add_invoice(student, "S6", semester="2030/2031", deleted=True)

    async with get_session_factory()() as db:
        semesters = await ReportService(db, lambda: NOW).semesters()

    assert semesters == ["2026/2027", "2025/2026", "2024/2025"]


# --- messages ------------------------------------------------------------------------------


async def test_message_counts_weekly_use_scheduled_date_window() -> None:
    student = await add_student("2201")
    invoice = await add_invoice(student, "I1")
    await add_message(student, invoice, status="sent", scheduled=TODAY)
    await add_message(student, invoice, status="sent", scheduled=TODAY - timedelta(days=6))
    await add_message(student, invoice, status="failed", scheduled=TODAY - timedelta(days=3))
    await add_message(student, invoice, status="queued", scheduled=TODAY + timedelta(days=0))
    await add_message(student, invoice, status="sent", scheduled=TODAY - timedelta(days=7))

    messages = (await build()).summary.messages

    assert (messages.sent, messages.failed, messages.queued) == (2, 1, 1)
    assert (messages.delivered, messages.read, messages.sending, messages.cancelled) == (0, 0, 0, 0)


async def test_message_counts_semester_follow_the_invoice_semester() -> None:
    student = await add_student("2201")
    in_a = await add_invoice(student, "A1", semester=SEM_A)
    in_b = await add_invoice(student, "B1", semester=SEM_B)
    long_ago = TODAY - timedelta(days=90)
    await add_message(student, in_a, status="delivered", scheduled=long_ago)
    await add_message(student, in_b, status="read", scheduled=long_ago)
    await add_message(student, in_b, status="read", scheduled=long_ago - timedelta(days=30))

    messages = (await build(ReportPreset.SEMESTER, SEM_B)).summary.messages

    assert (messages.read, messages.delivered) == (2, 0)


async def test_deleted_messages_invoices_and_students_are_not_counted() -> None:
    student = await add_student("2201")
    gone_student = await add_student("2202", deleted=True)
    live = await add_invoice(student, "LIVE")
    gone_invoice = await add_invoice(student, "GONE", deleted=True)
    other = await add_invoice(gone_student, "OTHER")
    await add_message(student, live, status="sent")
    await add_message(student, live, status="sent", deleted=True)
    await add_message(student, gone_invoice, status="sent")
    await add_message(gone_student, other, status="sent")

    assert (await build()).summary.messages.sent == 1


async def test_paid_after_message_counts_only_messages_on_or_before_payment_day() -> None:
    student = await add_student("2201")
    paid_yesterday = TODAY - timedelta(days=1)

    same_day = await add_invoice(student, "SAME", status="paid", paid_at=paid_yesterday)
    await add_message(student, same_day, status="sent", sent_at=at(2026, 11, 1))

    after = await add_invoice(student, "AFTER", status="paid", paid_at=TODAY - timedelta(days=3))
    await add_message(student, after, status="sent", sent_at=at(2026, 10, 31))

    not_reached = await add_invoice(student, "NOT-REACHED", status="paid", paid_at=paid_yesterday)
    await add_message(student, not_reached, status="queued")
    await add_message(student, not_reached, status="failed")
    await add_message(student, not_reached, status="sending")

    earlier_and_later = await add_invoice(student, "TWO", status="paid", paid_at=paid_yesterday)
    await add_message(student, earlier_and_later, status="delivered", sent_at=at(2026, 10, 29))
    await add_message(student, earlier_and_later, status="read", sent_at=at(2026, 11, 2))

    assert (await build()).summary.paid_after_message == 2


async def test_paid_after_message_scope_is_the_payment_window_when_weekly() -> None:
    student = await add_student("2201")
    old = await add_invoice(student, "OLD", status="paid", paid_at=TODAY - timedelta(days=20))
    await add_message(student, old, status="sent", sent_at=at(2026, 10, 1))

    assert (await build()).summary.paid_after_message == 0
    assert (await build(ReportPreset.SEMESTER, SEM_A)).summary.paid_after_message == 1


async def test_paid_after_message_uses_the_jakarta_calendar_day() -> None:
    student = await add_student("2201")
    paid = date(2026, 11, 1)
    # 18:00 UTC on 1 November is 01:00 on 2 November in Jakarta: sent after the payment day.
    late = await add_invoice(student, "LATE", status="paid", paid_at=paid)
    await add_message(student, late, status="sent", sent_at=at(2026, 11, 1, hour=18))
    # 16:00 UTC on 1 November is 23:00 in Jakarta: still the payment day.
    on_time = await add_invoice(student, "ON-TIME", status="paid", paid_at=paid)
    await add_message(student, on_time, status="sent", sent_at=at(2026, 11, 1, hour=16))

    assert (await build()).summary.paid_after_message == 1


async def test_arrears_rows_report_messages_sent_and_last_message() -> None:
    student = await add_student("2201")
    with_messages = await add_invoice(student, "WITH", due=TODAY - timedelta(days=20))
    without = await add_invoice(student, "WITHOUT", due=TODAY - timedelta(days=5))
    await add_message(student, with_messages, status="sent", sent_at=at(2026, 10, 20))
    await add_message(student, with_messages, status="delivered", sent_at=at(2026, 10, 25))
    await add_message(student, with_messages, status="failed", sent_at=at(2026, 10, 30))
    await add_message(student, with_messages, status="queued")
    await add_message(student, with_messages, status="read", sent_at=at(2026, 10, 31), deleted=True)
    assert without

    rows = {row.invoice_code: row for row in (await build()).arrears}

    with_row = rows["WITH"]
    assert with_row.messages_sent == 2
    assert with_row.last_message_date == date(2026, 10, 25)
    assert with_row.last_message_status == "delivered"
    without_row = rows["WITHOUT"]
    assert (without_row.messages_sent, without_row.last_message_date) == (0, None)
    assert without_row.last_message_status is None
