from collections.abc import Sequence
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import NOT_FOUND
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.users.model import User
from app.shared.clock import Clock, local_today
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .constants import AUDIT_ENTITY_INVOICE, AUDIT_INVOICE_STATUS_CHANGED, InvoiceStatus
from .model import Invoice
from .repository import InvoiceRepository


class InvoiceService:
    def __init__(self, db: AsyncSession, clock: Clock = utc_now) -> None:
        self.db = db
        self.clock = clock
        self.invoices = InvoiceRepository(db)

    async def list(
        self,
        params: ListParams,
        *,
        status: InvoiceStatus | None = None,
        semester: str | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        student_id: int | None = None,
    ) -> tuple[Sequence[Invoice], int]:
        return await self.invoices.search(
            params,
            status=status,
            semester=semester,
            due_from=due_from,
            due_to=due_to,
            student_id=student_id,
        )

    async def get(self, invoice_id: int) -> Invoice:
        invoice = await self.invoices.get(invoice_id)
        if invoice is None:
            raise AppException("Tagihan tidak ditemukan.", status_code=404, code=NOT_FOUND)
        return invoice

    async def set_status(self, invoice_id: int, new_status: InvoiceStatus, actor: User) -> Invoice:
        """Manual paid/unpaid correction. Re-sending the current status changes nothing."""
        invoice = await self.get(invoice_id)
        old_status = invoice.status
        if old_status == new_status.value:
            return invoice

        now = self.clock()
        invoice.status = new_status.value
        if new_status is InvoiceStatus.PAID:
            invoice.paid_at = local_today(now, settings.app_timezone)  # paid_via stays as is
        else:
            invoice.paid_at = None
            invoice.paid_via = None
        invoice.updated_by = actor.email
        invoice.updated_at = now

        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_INVOICE_STATUS_CHANGED,
            entity=AUDIT_ENTITY_INVOICE,
            entity_id=invoice.id,
            detail={"from": old_status, "to": new_status.value},
        )
        await self.db.commit()
        return invoice
