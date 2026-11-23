import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

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
from app.modules.messages.log_repository import chunk_ids
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
from app.modules.templates.rendering import build_message_body, fill_greeting
from app.modules.users.model import User
from app.shared.clock import Clock, as_utc, local_now, local_today
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
        breakdowns_query = select(Invoice.breakdown).where(
            Invoice.deleted_at.is_(None),
            Invoice.breakdown.is_not(None),
        )
        cohorts = [c for c in (await self.db.scalars(cohorts_query)) if c]
        study_programs = [p for p in (await self.db.scalars(study_programs_query)) if p]
        semesters = [s for s in (await self.db.scalars(semesters_query)) if s]
        component_set: set[str] = set()
        for breakdown in await self.db.scalars(breakdowns_query):
            if not isinstance(breakdown, list):
                continue
            for item in breakdown:
                if isinstance(item, dict):
                    label = str(item.get("label", "")).strip()
                    if label:
                        component_set.add(label)
        return ManualFilterOptionsRead(
            cohorts=cohorts,
            study_programs=study_programs,
            semesters=semesters,
            components=sorted(component_set),
        )

    async def _get_template(self, template_id: int) -> MessageTemplate:
        template = await self.db.get(MessageTemplate, template_id)
        if template is None:
            raise AppException("Templat pesan tidak ditemukan.", status_code=404, code=NOT_FOUND)
        if not template.is_active:
            raise AppException("Templat pesan tidak aktif.", status_code=422, code=VALIDATION_ERROR)
        return template

    def _build_invoices_query(
        self,
        req: ManualMessagePreviewRequest | ManualMessageSendRequest,
        *,
        lock_rows: bool = False,
    ):
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
        has_components = bool(req.components and any(c.strip() for c in req.components))
        if not has_components:
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

        stmt = stmt.order_by(Invoice.due_date.asc(), Student.nim.asc())
        if lock_rows and self.db.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(of=Invoice)
        return stmt

    async def _pending_invoice_ids(self, invoice_ids: Sequence[int]) -> set[int]:
        """Invoices that already have a manual message waiting to go out (queued or sending)."""
        unique_ids = sorted(set(invoice_ids))
        if not unique_ids:
            return set()

        pending: set[int] = set()
        for chunk in chunk_ids(unique_ids):
            rows = await self.db.scalars(
                select(Message.invoice_id)
                .join(Invoice, Message.invoice_id == Invoice.id)
                .join(Student, Invoice.student_id == Student.id)
                .where(
                    Message.invoice_id.in_(chunk),
                    Message.kind == MessageKind.MANUAL.value,
                    Message.status.in_((MessageStatus.QUEUED.value, MessageStatus.SENDING.value)),
                    Message.deleted_at.is_(None),
                    Invoice.deleted_at.is_(None),
                    Student.deleted_at.is_(None),
                )
            )
            pending.update(rows)
        return pending

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

    @staticmethod
    def _normalize_components(components: Sequence[str] | None) -> set[str]:
        if not components:
            return set()
        return {c.strip() for c in components if c and c.strip()}

    @staticmethod
    def _resolve_invoice_billing(
        invoice: Invoice,
        selected_components: set[str],
        *,
        amount_min: int | None,
        amount_max: int | None,
    ) -> tuple[list[dict[str, Any]] | None, int] | None:
        """Return ``(effective_breakdown, effective_amount)`` or ``None`` if filtered out."""
        if not selected_components:
            return invoice.breakdown, invoice.amount
        if not invoice.breakdown:
            return None
        matched = [
            {"label": str(item["label"]).strip(), "amount": int(item["amount"])}
            for item in invoice.breakdown
            if isinstance(item, dict)
            and str(item.get("label", "")).strip() in selected_components
            and item.get("amount") is not None
        ]
        if not matched:
            return None
        total = sum(int(item["amount"]) for item in matched)
        if amount_min is not None and total < amount_min:
            return None
        if amount_max is not None and total > amount_max:
            return None
        return matched, total

    async def preview(self, req: ManualMessagePreviewRequest) -> ManualMessagePreviewRead:
        template = await self._get_template(req.template_id)
        settings = await SettingsService(self.db).get()
        now = as_utc(self.clock())
        today = local_today(now, config.app_timezone)
        selected_components = self._normalize_components(req.components)

        stmt = self._build_invoices_query(req)
        raw_invoices = list((await self.db.scalars(stmt)).all())
        resolved_invoices: list[tuple[Invoice, list[dict[str, Any]] | None, int]] = []
        for inv in raw_invoices:
            resolved = self._resolve_invoice_billing(
                inv,
                selected_components,
                amount_min=req.amount_min,
                amount_max=req.amount_max,
            )
            if resolved is not None:
                resolved_invoices.append((inv, resolved[0], resolved[1]))

        pending = await self._pending_invoice_ids([inv.id for inv, _, _ in resolved_invoices])

        breakdown = ExcludedBreakdown()
        items: list[ManualRecipientItem] = []
        sample_preview: SamplePreview | None = None
        eligible_count = 0
        excluded_count = 0

        for invoice, eff_breakdown, eff_amount in resolved_invoices:
            is_eligible, exclude_reason, category = self._check_eligibility(invoice, today, pending)

            if is_eligible:
                eligible_count += 1
                if sample_preview is None:
                    rendered_body = build_message_body(
                        template,
                        student=invoice.student,
                        invoice=invoice,
                        settings=settings,
                        amount_override=eff_amount,
                        breakdown_override=eff_breakdown,
                    )
                    # The queued body keeps {{salam}} for the sender; show what a send now says.
                    rendered_body = fill_greeting(
                        rendered_body, local_now(now, config.app_timezone)
                    )
                    sample_preview = SamplePreview(
                        student_name=invoice.student.name,
                        nim=invoice.student.nim,
                        phone_masked=mask_phone(invoice.student.phone),
                        invoice_code=invoice.invoice_code,
                        amount=eff_amount,
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
                    amount=eff_amount,
                    breakdown=eff_breakdown,
                    due_date=invoice.due_date,
                    invoice_status=invoice.status,
                    is_eligible=is_eligible,
                    exclude_reason=exclude_reason,
                )
            )

        return ManualMessagePreviewRead(
            total_matched=len(resolved_invoices),
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
        selected_components = self._normalize_components(req.components)

        stmt = self._build_invoices_query(req, lock_rows=True)
        raw_invoices = list((await self.db.scalars(stmt)).all())
        resolved_invoices: list[tuple[Invoice, list[dict[str, Any]] | None, int]] = []
        for inv in raw_invoices:
            resolved = self._resolve_invoice_billing(
                inv,
                selected_components,
                amount_min=req.amount_min,
                amount_max=req.amount_max,
            )
            if resolved is not None:
                resolved_invoices.append((inv, resolved[0], resolved[1]))

        pending = await self._pending_invoice_ids([inv.id for inv, _, _ in resolved_invoices])

        eligible_invoices: list[tuple[Invoice, list[dict[str, Any]] | None, int]] = []
        excluded_count = 0

        for invoice, eff_breakdown, eff_amount in resolved_invoices:
            is_eligible, _, _ = self._check_eligibility(invoice, today, pending)
            if is_eligible:
                eligible_invoices.append((invoice, eff_breakdown, eff_amount))
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

        for invoice, eff_breakdown, eff_amount in eligible_invoices:
            rendered_body = build_message_body(
                template,
                student=invoice.student,
                invoice=invoice,
                settings=settings,
                amount_override=eff_amount,
                breakdown_override=eff_breakdown,
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

        audit_detail: dict[str, Any] = {
            "batch_ref": batch_ref,
            "template_id": template.id,
            "template_name": template.name,
            "queued_count": len(eligible_invoices),
            "excluded_count": excluded_count,
            "total_matched": len(resolved_invoices),
        }
        if selected_components:
            audit_detail["components"] = sorted(selected_components)

        await record_audit(
            self.db,
            actor=actor.email,
            action="messages:manual_send",
            entity="messages",
            entity_id=batch_ref,
            detail=audit_detail,
        )

        await self.db.commit()

        return ManualMessageSendRead(
            batch_ref=batch_ref,
            queued_count=len(eligible_invoices),
            excluded_count=excluded_count,
            total_matched=len(resolved_invoices),
        )
