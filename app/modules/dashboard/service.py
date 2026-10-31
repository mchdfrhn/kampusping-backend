from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.messages.constants import MessageStatus
from app.modules.messages.model import Message
from app.modules.settings.service import SettingsService
from app.modules.students.model import Student
from app.shared.clock import Clock, as_utc, local_today

from .schema import DashboardSummary, InvoiceSummary, MessageSummary, WarningsSummary


class DashboardService:
    """Read-only aggregates for the dashboard: a handful of statements, whatever the row count."""

    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    async def summary(self, semester: str | None) -> DashboardSummary:
        now = self.clock()
        today = local_today(now, settings.app_timezone)
        return DashboardSummary(
            invoices=await self._invoices(semester, today),
            messages=await self._messages(semester),
            warnings=await self._warnings(),
            semester=semester,
            generated_at=as_utc(now),
        )

    async def _invoices(self, semester: str | None, today: date) -> InvoiceSummary:
        unpaid = Invoice.status == InvoiceStatus.UNPAID.value
        paid = Invoice.status == InvoiceStatus.PAID.value
        overdue = and_(unpaid, Invoice.due_date < today)
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
            )
            .join(Student, Student.id == Invoice.student_id)
            .where(Invoice.deleted_at.is_(None), Student.deleted_at.is_(None))
        )
        if semester is not None:
            query = query.where(Invoice.semester == semester)
        row = (await self.db.execute(query)).one()
        return InvoiceSummary(
            total=row[0],
            total_amount=row[1],
            paid=row[2],
            paid_amount=row[3],
            unpaid=row[4],
            unpaid_amount=row[5],
            overdue=row[6],
            overdue_amount=row[7],
        )

    async def _messages(self, semester: str | None) -> MessageSummary:
        query = (
            select(Message.status, func.count(Message.id))
            .join(Invoice, Invoice.id == Message.invoice_id)
            .join(Student, Student.id == Message.student_id)
            .where(
                Message.deleted_at.is_(None),
                Invoice.deleted_at.is_(None),
                Student.deleted_at.is_(None),
            )
            .group_by(Message.status)
        )
        if semester is not None:
            query = query.where(Invoice.semester == semester)
        counts = dict((await self.db.execute(query)).tuples().all())
        return MessageSummary(
            **{status.value: counts.get(status.value, 0) for status in MessageStatus}
        )

    async def _warnings(self) -> WarningsSummary:
        row = await SettingsService(self.db).get()
        await self.db.commit()  # persists the default settings row when it was just created
        return WarningsSummary(
            paused_reason=row.paused_reason, auto_send_enabled=row.auto_send_enabled
        )
