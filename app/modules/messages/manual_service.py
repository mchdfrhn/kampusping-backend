import uuid
from datetime import date

from sqlalchemy import distinct, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings as config
from app.core.constants import NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice
from app.modules.messages.constants import MessageKind, MessageStatus
from app.modules.messages.model import Message
from app.modules.messages.schema import (
    ExcludedBreakdown,
    ManualFilterOptionsRead,
    ManualMessagePreviewRead,
    ManualMessagePreviewRequest,
    ManualMessageSendRead,
    ManualMessageSendRequest,
    ManualRecipientItem,
    SamplePreview,
)
from app.modules.messages.send_rules import is_exempt
from app.modules.settings.service import SettingsService
from app.modules.students.constants import PhoneStatus
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate
from app.modules.templates.rendering import build_message_body
from app.modules.users.model import User
from app.shared.clock import Clock, as_utc, local_today
from app.shared.phone import mask_phone
from app.shared.utils import utc_now


class ManualMessageService:
    def __init__(self, db: AsyncSession, clock: Clock = utc_now) -> None:
        self.db = db
        self.clock = clock

    async def get_filter_options(self) -> ManualFilterOptionsRead:
        cohorts_query = (
            select(distinct(Student.cohort))
            .where(Student.cohort.is_not(None), Student.cohort != "")
            .order_by(Student.cohort.desc())
        )
        study_programs_query = (
            select(distinct(Student.study_program))
            .where(Student.study_program.is_not(None), Student.study_program != "")
            .order_by(Student.study_program.asc())
        )
        semesters_query = (
            select(distinct(Invoice.semester))
            .where(Invoice.semester.is_not(None), Invoice.semester != "")
            .order_by(Invoice.semester.desc())
        )
        cohorts = [c for c in (await self.db.scalars(cohorts_query)) if c]
        study_programs = [p for p in (await self.db.scalars(study_programs_query)) if p]
        semesters = [s for s in (await self.db.scalars(semesters_query)) if s]
        return ManualFilterOptionsRead(
            cohorts=cohorts,
            study_programs=study_programs,
            semesters=semesters,
        )

    async def _get_template(self, template_id: int) -> MessageTemplate:
        template = await self.db.get(MessageTemplate, template_id)
        if template is None:
            raise AppException("Templat pesan tidak ditemukan.", status_code=404, code=NOT_FOUND)
        if not template.is_active:
            raise AppException("Templat pesan tidak aktif.", status_code=422, code=VALIDATION_ERROR)
        return template

    def _build_invoices_query(self, req: ManualMessagePreviewRequest | ManualMessageSendRequest):
        stmt = (
            select(Invoice)
            .join(Invoice.student)
            .options(selectinload(Invoice.student))
            .where(Invoice.deleted_at.is_(None), Student.deleted_at.is_(None))
        )

        if req.cohort:
            stmt = stmt.where(Student.cohort == req.cohort)
        if req.study_program:
            stmt = stmt.where(Student.study_program == req.study_program)
        if req.semester:
            stmt = stmt.where(Invoice.semester == req.semester)
        if req.amount_min is not None:
            stmt = stmt.where(Invoice.amount >= req.amount_min)
        if req.amount_max is not None:
            stmt = stmt.where(Invoice.amount <= req.amount_max)
        if req.status and req.status != "all":
            stmt = stmt.where(Invoice.status == req.status)
        elif not req.status:
            stmt = stmt.where(Invoice.status == InvoiceStatus.UNPAID.value)

        if req.search:
            pattern = f"%{req.search.strip()}%"
            stmt = stmt.where(
                or_(
                    Student.name.ilike(pattern),
                    Student.nim.ilike(pattern),
                    Invoice.invoice_code.ilike(pattern),
                )
            )

        if req.invoice_ids:
            stmt = stmt.where(Invoice.id.in_(req.invoice_ids))

        return stmt.order_by(Invoice.due_date.asc(), Student.nim.asc())

    async def _pending_invoice_ids(self) -> set[int]:
        """Invoices that already have a manual message waiting to go out (queued or sending)."""
        rows = await self.db.scalars(
            select(Message.invoice_id).where(
                Message.kind == MessageKind.MANUAL.value,
                Message.status.in_((MessageStatus.QUEUED.value, MessageStatus.SENDING.value)),
                Message.deleted_at.is_(None),
            )
        )
        return set(rows)

    def _check_eligibility(
        self, invoice: Invoice, today: date, pending_invoice_ids: set[int]
    ) -> tuple[bool, str | None, str | None]:
        """Returns (is_eligible, exclude_reason, breakdown_category).

        Mirrors the sender's re-check (``send_rules.recheck``) so the preview never promises
        a message the sender would cancel; the queue check keeps a double submit from
        duplicating a message that has not gone out yet.
        """
        if invoice.status == InvoiceStatus.PAID.value:
            return False, "Sudah lunas", "paid"
        if invoice.student.opted_out_at is not None:
            return False, "Mahasiswa opt-out", "opted_out"
        if is_exempt(invoice, today):
            return False, "Dispensasi aktif", "exempt"
        if not invoice.student.phone or invoice.student.phone_status != PhoneStatus.VALID.value:
            return False, "Nomor WhatsApp tidak valid atau kosong", "invalid_phone"
        if invoice.id in pending_invoice_ids:
            return False, "Sudah dalam antrean", "already_queued"
        return True, None, None

    async def preview(self, req: ManualMessagePreviewRequest) -> ManualMessagePreviewRead:
        template = await self._get_template(req.template_id)
        settings = await SettingsService(self.db).get()
        now = as_utc(self.clock())
        today = local_today(now, config.app_timezone)

        stmt = self._build_invoices_query(req)
        invoices = list((await self.db.scalars(stmt)).all())
        pending = await self._pending_invoice_ids()

        breakdown = ExcludedBreakdown()
        items: list[ManualRecipientItem] = []
        sample_preview: SamplePreview | None = None
        eligible_count = 0
        excluded_count = 0

        for invoice in invoices:
            is_eligible, exclude_reason, category = self._check_eligibility(invoice, today, pending)

            if is_eligible:
                eligible_count += 1
                if sample_preview is None:
                    rendered_body = build_message_body(
                        template, student=invoice.student, invoice=invoice, settings=settings
                    )
                    sample_preview = SamplePreview(
                        student_name=invoice.student.name,
                        nim=invoice.student.nim,
                        phone_masked=mask_phone(invoice.student.phone),
                        invoice_code=invoice.invoice_code,
                        amount=invoice.amount,
                        body=rendered_body,
                    )
            else:
                excluded_count += 1
                if category == "paid":
                    breakdown.paid += 1
                elif category == "opted_out":
                    breakdown.opted_out += 1
                elif category == "exempt":
                    breakdown.exempt += 1
                elif category == "invalid_phone":
                    breakdown.invalid_phone += 1
                elif category == "already_queued":
                    breakdown.already_queued += 1

            items.append(
                ManualRecipientItem(
                    invoice_id=invoice.id,
                    invoice_code=invoice.invoice_code,
                    student_id=invoice.student_id,
                    student_name=invoice.student.name,
                    nim=invoice.student.nim,
                    phone_masked=mask_phone(invoice.student.phone),
                    study_program=invoice.student.study_program,
                    cohort=invoice.student.cohort,
                    semester=invoice.semester,
                    amount=invoice.amount,
                    due_date=invoice.due_date,
                    invoice_status=invoice.status,
                    is_eligible=is_eligible,
                    exclude_reason=exclude_reason,
                )
            )

        return ManualMessagePreviewRead(
            total_matched=len(invoices),
            eligible_count=eligible_count,
            excluded_count=excluded_count,
            excluded_breakdown=breakdown,
            sample_preview=sample_preview,
            items=items,
        )

    async def send(self, req: ManualMessageSendRequest, actor: User) -> ManualMessageSendRead:
        template = await self._get_template(req.template_id)
        settings = await SettingsService(self.db).get()
        now = as_utc(self.clock())
        today = local_today(now, config.app_timezone)

        stmt = self._build_invoices_query(req)
        invoices = list((await self.db.scalars(stmt)).all())
        pending = await self._pending_invoice_ids()

        eligible_invoices: list[Invoice] = []
        excluded_count = 0

        for invoice in invoices:
            is_eligible, _, _ = self._check_eligibility(invoice, today, pending)
            if is_eligible:
                eligible_invoices.append(invoice)
            else:
                excluded_count += 1

        if not eligible_invoices:
            raise AppException(
                "Tidak ada penerima yang memenuhi syarat untuk dikirimi pesan.",
                status_code=400,
                code=VALIDATION_ERROR,
            )

        batch_ref = f"MANUAL-{today.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        actor_name = actor.email  # not nullable; the audit entry below uses it too

        for invoice in eligible_invoices:
            rendered_body = build_message_body(
                template, student=invoice.student, invoice=invoice, settings=settings
            )
            msg = Message(
                student_id=invoice.student_id,
                invoice_id=invoice.id,
                rule_id=None,
                kind=MessageKind.MANUAL.value,
                batch_ref=batch_ref,
                scheduled_date=today,
                body=rendered_body,
                status=MessageStatus.QUEUED.value,
                queued_at=now,
                created_at=now,
                updated_at=now,
                created_by=actor_name,
                updated_by=actor_name,
            )
            self.db.add(msg)

        await record_audit(
            self.db,
            actor=actor.email,
            action="messages:manual_send",
            entity="messages",
            entity_id=batch_ref,
            detail={
                "batch_ref": batch_ref,
                "template_id": template.id,
                "template_name": template.name,
                "queued_count": len(eligible_invoices),
                "excluded_count": excluded_count,
                "total_matched": len(invoices),
            },
        )

        await self.db.commit()

        return ManualMessageSendRead(
            batch_ref=batch_ref,
            queued_count=len(eligible_invoices),
            excluded_count=excluded_count,
            total_matched=len(invoices),
        )
