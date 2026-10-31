from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CONFLICT, NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.schedules.repository import ScheduleRuleRepository
from app.modules.users.model import User
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .constants import (
    AUDIT_ENTITY_TEMPLATE,
    AUDIT_TEMPLATE_CREATED,
    AUDIT_TEMPLATE_DELETED,
    AUDIT_TEMPLATE_UPDATED,
)
from .model import MessageTemplate
from .rendering import SAMPLE_CONTEXT, render_body, unknown_variables, with_opt_out_footer
from .repository import TemplateRepository
from .schema import TemplatePreviewResponse, TemplateWrite


class TemplateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.templates = TemplateRepository(db)

    async def list(
        self, params: ListParams, *, is_active: bool | None = None
    ) -> tuple[Sequence[MessageTemplate], int]:
        return await self.templates.search(params, is_active=is_active)

    async def get(self, template_id: int) -> MessageTemplate:
        template = await self.templates.get(template_id)
        if template is None:
            raise AppException("Templat tidak ditemukan.", status_code=404, code=NOT_FOUND)
        return template

    def preview(self, body: str) -> TemplatePreviewResponse:
        """Render ``body`` with fixed sample data. Unknown variables are reported, not rejected."""
        return TemplatePreviewResponse(
            rendered=with_opt_out_footer(render_body(body, SAMPLE_CONTEXT)),
            unknown_variables=unknown_variables(body),
        )

    async def create(self, data: TemplateWrite, actor: User) -> MessageTemplate:
        self._ensure_known_variables(data.body)
        await self._ensure_name_available(data.name)
        template = MessageTemplate(
            name=data.name,
            body=data.body,
            is_active=data.is_active,
            created_by=actor.email,
            updated_by=actor.email,
        )
        await self.templates.add(template)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_TEMPLATE_CREATED,
            entity=AUDIT_ENTITY_TEMPLATE,
            entity_id=template.id,
            detail={"name": template.name},
        )
        await self.db.commit()
        return template

    async def update(self, template_id: int, data: TemplateWrite, actor: User) -> MessageTemplate:
        template = await self.get(template_id)
        self._ensure_known_variables(data.body)
        if data.name.lower() != template.name.lower():
            await self._ensure_name_available(data.name, exclude_id=template.id)

        changed: list[str] = []
        for field in ("name", "body", "is_active"):
            value = getattr(data, field)
            if getattr(template, field) != value:
                setattr(template, field, value)
                changed.append(field)

        template.updated_by = actor.email
        template.updated_at = utc_now()
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_TEMPLATE_UPDATED,
            entity=AUDIT_ENTITY_TEMPLATE,
            entity_id=template.id,
            detail={"changed": changed},
        )
        await self.db.commit()
        return template

    async def delete(self, template_id: int, actor: User) -> None:
        template = await self.get(template_id)
        if await ScheduleRuleRepository(self.db).exists_for_template(template.id):
            raise AppException(
                "Template masih dipakai oleh aturan jadwal.", status_code=409, code=CONFLICT
            )
        await self.templates.soft_delete(template, actor.email)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_TEMPLATE_DELETED,
            entity=AUDIT_ENTITY_TEMPLATE,
            entity_id=template.id,
            detail={"name": template.name},
        )
        await self.db.commit()

    @staticmethod
    def _ensure_known_variables(body: str) -> None:
        unknown = unknown_variables(body)
        if unknown:
            names = ", ".join("{{" + name + "}}" for name in unknown)
            raise AppException(
                f"Variabel tidak dikenal: {names}.",
                status_code=422,
                code=VALIDATION_ERROR,
                details=unknown,
            )

    async def _ensure_name_available(self, name: str, *, exclude_id: int | None = None) -> None:
        if await self.templates.get_by_name(name, exclude_id=exclude_id):
            raise AppException("Templat dengan nama ini sudah ada.", status_code=409, code=CONFLICT)
