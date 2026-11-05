from datetime import date, timedelta

from sqlalchemy import ColumnElement, and_, distinct, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.messages.constants import MessageStatus
from app.modules.students.model import Student
from app.shared.clock import Clock, as_utc, local_today

from .constants import (
    NO_COHORT_LABEL,
    NO_PROGRAM_LABEL,
    WEEKLY_WINDOW_DAYS,
    ReportPreset,
)
from .schema import (
    ArrearsRow,
    GroupRow,
    InvoiceFigures,
    MessageFigures,
    PaymentsInPeriod,
    ReportData,
    ReportSummary,
)

Scope = list[ColumnElement[bool]]


def _percent(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


def _label(value: str | None, fallback: str) -> str:
    return (value or "").strip() or fallback


class ReportService:
    """Read-only aggregates behind the billing report: a fixed number of statements per report."""

    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    async def semesters(self) -> list[str]:
        query = (
            select(distinct(Invoice.semester))
            .join(Student, Student.id == Invoice.student_id)
            .where(
                Invoice.deleted_at.is_(None),
                Student.deleted_at.is_(None),
                Invoice.semester != "",
            )
            .order_by(Invoice.semester.desc())
        )
        return list(await self.db.scalars(query))

    async def build(
        self, preset: ReportPreset, semester: str | None, generated_by: str
    ) -> ReportData:
        if preset is ReportPreset.SEMESTER and not semester:
            raise AppException(
                "Semester wajib dipilih untuk laporan per semester.",
                status_code=422,
                code=VALIDATION_ERROR,
            )
        now = self.clock()
        today = local_today(now, settings.app_timezone)
        weekly = preset is ReportPreset.WEEKLY
        scope_semester = None if weekly else semester
        period_start = today - timedelta(days=WEEKLY_WINDOW_DAYS - 1) if weekly else None
        period_end = today if weekly else None

        scope = self._scope(scope_semester)
        payments = (
            await self._payments_in_period(scope, period_start, period_end)
            if period_start is not None and period_end is not None
            else None
        )
        return ReportData(
            preset=preset,
            semester=scope_semester,
            period_start=period_start,
            period_end=period_end,
            generated_at=as_utc(now),
            generated_by=generated_by,
            summary=ReportSummary(
                invoices=await self._invoice_figures(scope, today),
                messages=self._message_figures(),
                paid_after_message=0,
                payments_in_period=payments,
            ),
            groups=await self._groups(scope, today),
            arrears=await self._arrears(scope, today),
        )

    # --- predicates shared by every statement -------------------------------------------------

    @staticmethod
    def _scope(semester: str | None) -> Scope:
        """Live invoices of live students, optionally of one semester."""
        scope: Scope = [Invoice.deleted_at.is_(None), Student.deleted_at.is_(None)]
        if semester is not None:
            scope.append(Invoice.semester == semester)
        return scope

    @staticmethod
    def _exemption(today: date) -> ColumnElement[bool]:
        """AB-05, the rule the sender uses: a reason and no end date, or an end date not passed."""
        return and_(
            Invoice.exempt_reason.is_not(None),
            or_(Invoice.exempt_until.is_(None), Invoice.exempt_until >= today),
        )

    @staticmethod
    def _unpaid() -> ColumnElement[bool]:
        return Invoice.status == InvoiceStatus.UNPAID.value

    @classmethod
    def _overdue(cls, today: date) -> ColumnElement[bool]:
        return and_(cls._unpaid(), Invoice.due_date < today, not_(cls._exemption(today)))

    # --- statements ---------------------------------------------------------------------------

    async def _invoice_figures(self, scope: Scope, today: date) -> InvoiceFigures:
        paid = Invoice.status == InvoiceStatus.PAID.value
        unpaid = self._unpaid()
        overdue = self._overdue(today)
        exempt = and_(unpaid, self._exemption(today))
        query = (
            select(
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.amount), 0),
                func.count(Invoice.id).filter(paid),
                func.coalesce(func.sum(Invoice.amount).filter(paid), 0),
                func.count(Invoice.id).filter(unpaid),
                func.coalesce(func.sum(Invoice.amount).filter(unpaid), 0),
                func.count(Invoice.id).filter(overdue),
                func.coalesce(func.sum(Invoice.amount).filter(overdue), 0),
                func.count(Invoice.id).filter(exempt),
                func.coalesce(func.sum(Invoice.amount).filter(exempt), 0),
            )
            .join(Student, Student.id == Invoice.student_id)
            .where(*scope)
        )
        row = (await self.db.execute(query)).one()
        return InvoiceFigures(
            total=row[0],
            total_amount=row[1],
            paid=row[2],
            paid_amount=row[3],
            unpaid=row[4],
            unpaid_amount=row[5],
            overdue=row[6],
            overdue_amount=row[7],
            exempt=row[8],
            exempt_amount=row[9],
            paid_percent_count=_percent(row[2], row[0]),
            paid_percent_amount=_percent(row[3], row[1]),
        )

    @staticmethod
    def _message_figures() -> MessageFigures:
        return MessageFigures(**{status.value: 0 for status in MessageStatus})

    async def _payments_in_period(self, scope: Scope, start: date, end: date) -> PaymentsInPeriod:
        query = (
            select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.amount), 0))
            .join(Student, Student.id == Invoice.student_id)
            .where(
                *scope,
                Invoice.status == InvoiceStatus.PAID.value,
                Invoice.paid_at >= start,
                Invoice.paid_at <= end,
            )
        )
        count, amount = (await self.db.execute(query)).one()
        return PaymentsInPeriod(count=count, amount=amount)

    async def _groups(self, scope: Scope, today: date) -> list[GroupRow]:
        paid = Invoice.status == InvoiceStatus.PAID.value
        overdue = self._overdue(today)
        query = (
            select(
                Student.study_program,
                Student.cohort,
                func.count(Invoice.id),
                func.count(Invoice.id).filter(paid),
                func.count(Invoice.id).filter(self._unpaid()),
                func.count(Invoice.id).filter(overdue),
                func.coalesce(func.sum(Invoice.amount).filter(overdue), 0),
            )
            .join(Student, Student.id == Invoice.student_id)
            .where(*scope)
            .group_by(Student.study_program, Student.cohort)
        )
        # NULL and "" both read "(tanpa ...)", so rows that share a label are merged here.
        merged: dict[tuple[str, str], list[int]] = {}
        for program, cohort, *counts in (await self.db.execute(query)).all():
            key = (_label(program, NO_PROGRAM_LABEL), _label(cohort, NO_COHORT_LABEL))
            sums = merged.setdefault(key, [0] * len(counts))
            for index, value in enumerate(counts):
                sums[index] += value

        def order(key: tuple[str, str]) -> tuple[bool, str, bool, str]:
            program, cohort = key
            return (
                program == NO_PROGRAM_LABEL,
                program.casefold(),
                cohort == NO_COHORT_LABEL,
                cohort,
            )

        rows = []
        for (program, cohort), (total, paid_count, unpaid, overdue_count, overdue_sum) in sorted(
            merged.items(), key=lambda item: order(item[0])
        ):
            rows.append(
                GroupRow(
                    study_program=program,
                    cohort=cohort,
                    total=total,
                    paid=paid_count,
                    unpaid=unpaid,
                    overdue=overdue_count,
                    overdue_amount=overdue_sum,
                    paid_percent=_percent(paid_count, total),
                )
            )
        return rows

    async def _arrears(self, scope: Scope, today: date) -> list[ArrearsRow]:
        query = (
            select(
                Student.nim,
                Student.name,
                Student.study_program,
                Student.cohort,
                Invoice.semester,
                Invoice.invoice_code,
                Invoice.amount,
                Invoice.due_date,
            )
            .join(Student, Student.id == Invoice.student_id)
            .where(*scope, self._overdue(today))
            .order_by(Invoice.due_date.asc(), Student.nim.asc(), Invoice.invoice_code.asc())
        )
        rows = []
        for nim, name, program, cohort, semester, code, amount, due_date in (
            await self.db.execute(query)
        ).all():
            rows.append(
                ArrearsRow(
                    nim=nim,
                    name=name,
                    study_program=_label(program, NO_PROGRAM_LABEL),
                    cohort=_label(cohort, NO_COHORT_LABEL),
                    semester=semester,
                    invoice_code=code,
                    amount=amount,
                    due_date=due_date,
                    days_overdue=(today - due_date).days,
                    messages_sent=0,
                    last_message_date=None,
                    last_message_status=None,
                )
            )
        return rows
