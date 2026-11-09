from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.constants import NOT_FOUND
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.messages.constants import CancelReason, MessageStatus
from app.modules.messages.model import Message
from app.modules.students.model import Student
from app.modules.users.model import User
from app.shared.clock import Clock, local_today
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .constants import (
    AUDIT_ENTITY_INVOICE,
    AUDIT_INVOICE_DELETED_BY_FILE,
    AUDIT_INVOICE_STATUS_CHANGED,
    InvoiceStatus,
)
from .file_parser import extract_invoice_codes_from_file
from .model import Invoice
from .repository import InvoiceRepository
from .schema import (
    InvoiceDeleteByFilePreview,
    InvoiceDeleteByFileResult,
    InvoiceDeletePreviewItem,
    InvoiceFilterOptions,
)


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

    async def filter_options(self) -> InvoiceFilterOptions:
        return InvoiceFilterOptions(semesters=await self.invoices.distinct_semesters())

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

    async def preview_delete_by_file(
        self, filename: str, content: bytes
    ) -> InvoiceDeleteByFilePreview:
        codes = extract_invoice_codes_from_file(filename, content)
        return await self.preview_delete_by_codes(codes)

    async def preview_delete_by_codes(self, codes: Sequence[str]) -> InvoiceDeleteByFilePreview:
        if not codes:
            return InvoiceDeleteByFilePreview(
                total_codes_in_file=0,
                found_count=0,
                not_found_count=0,
                affected_messages_count=0,
                found_invoices=[],
                not_found_codes=[],
            )

        stmt = (
            select(Invoice)
            .join(Student, Student.id == Invoice.student_id)
            .where(Invoice.deleted_at.is_(None), Invoice.invoice_code.in_(codes))
            .options(selectinload(Invoice.student))
        )
        invoices = list((await self.db.scalars(stmt)).all())
        found_map = {inv.invoice_code: inv for inv in invoices}
        found_ids = [inv.id for inv in invoices]

        messages_count = 0
        if found_ids:
            msg_stmt = select(func.count(Message.id)).where(Message.invoice_id.in_(found_ids))
            messages_count = (await self.db.scalar(msg_stmt)) or 0

        found_items = [
            InvoiceDeletePreviewItem(
                id=inv.id,
                invoice_code=inv.invoice_code,
                student_nim=inv.student.nim,
                student_name=inv.student.name,
                semester=inv.semester,
                amount=inv.amount,
                due_date=inv.due_date,
                status=inv.status,
            )
            for inv in invoices
        ]

        not_found_codes = [c for c in codes if c not in found_map]

        return InvoiceDeleteByFilePreview(
            total_codes_in_file=len(codes),
            found_count=len(found_items),
            not_found_count=len(not_found_codes),
            affected_messages_count=messages_count,
            found_invoices=found_items,
            not_found_codes=not_found_codes,
        )

    async def delete_by_file(
        self, filename: str, content: bytes, *, hard_delete: bool, actor: User
    ) -> InvoiceDeleteByFileResult:
        codes = extract_invoice_codes_from_file(filename, content)
        return await self.delete_by_codes(
            codes, hard_delete=hard_delete, actor=actor, filename=filename
        )

    async def delete_by_codes(
        self,
        codes: Sequence[str],
        *,
        hard_delete: bool,
        actor: User,
        filename: str | None = None,
    ) -> InvoiceDeleteByFileResult:
        stmt = select(Invoice.id).where(
            Invoice.deleted_at.is_(None), Invoice.invoice_code.in_(codes)
        )
        found_ids = list((await self.db.scalars(stmt)).all())

        if not found_ids:
            return InvoiceDeleteByFileResult(
                deleted_invoices_count=0,
                affected_messages_count=0,
                not_found_count=len(codes),
            )

        now = self.clock()
        msg_stmt = select(func.count(Message.id)).where(Message.invoice_id.in_(found_ids))
        messages_count = (await self.db.scalar(msg_stmt)) or 0

        if hard_delete:
            if messages_count > 0:
                await self.db.execute(delete(Message).where(Message.invoice_id.in_(found_ids)))
            await self.db.execute(delete(Invoice).where(Invoice.id.in_(found_ids)))
        else:
            if messages_count > 0:
                await self.db.execute(
                    update(Message)
                    .where(
                        Message.invoice_id.in_(found_ids),
                        Message.status == MessageStatus.QUEUED.value,
                    )
                    .values(
                        status=MessageStatus.CANCELLED.value,
                        cancel_reason=CancelReason.MANUAL.value,
                        error="Tagihan dihapus via berkas",
                        updated_by=actor.email,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
            await self.db.execute(
                update(Invoice)
                .where(Invoice.id.in_(found_ids))
                .values(
                    deleted_at=now,
                    deleted_by=actor.email,
                    updated_at=now,
                    updated_by=actor.email,
                )
                .execution_options(synchronize_session=False)
            )

        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_INVOICE_DELETED_BY_FILE,
            entity=AUDIT_ENTITY_INVOICE,
            entity_id=None,
            detail={
                "deleted_count": len(found_ids),
                "messages_affected": messages_count,
                "hard_delete": hard_delete,
                "filename": filename,
            },
        )
        await self.db.commit()

        return InvoiceDeleteByFileResult(
            deleted_invoices_count=len(found_ids),
            affected_messages_count=messages_count,
            not_found_count=len(codes) - len(found_ids),
        )
