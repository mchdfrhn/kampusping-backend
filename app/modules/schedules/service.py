from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CONFLICT, NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.templates.model import MessageTemplate
from app.modules.templates.repository import TemplateRepository
from app.modules.users.model import User
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .constants import (
    AUDIT_ENTITY_RULE,
    AUDIT_RULE_CREATED,
    AUDIT_RULE_DELETED,
    AUDIT_RULE_UPDATED,
)
from .model import ScheduleRule
from .repository import ScheduleRuleRepository
from .schema import ScheduleRuleWrite


class ScheduleRuleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.rules = ScheduleRuleRepository(db)
        self.templates = TemplateRepository(db)

    async def list(
        self, params: ListParams, *, is_active: bool | None = None
    ) -> tuple[Sequence[ScheduleRule], int]:
        return await self.rules.search(params, is_active=is_active)

    async def get(self, rule_id: int) -> ScheduleRule:
        rule = await self.rules.get(rule_id)
        if rule is None:
            raise AppException("Aturan jadwal tidak ditemukan.", status_code=404, code=NOT_FOUND)
        return rule

    async def create(self, data: ScheduleRuleWrite, actor: User) -> ScheduleRule:
        template = await self._require_template(data.template_id)
        await self._ensure_offset_available(data.offset_days)
        rule = ScheduleRule(
            name=data.name,
            offset_days=data.offset_days,
            template=template,
            is_active=data.is_active,
            created_by=actor.email,
            updated_by=actor.email,
        )
        await self.rules.add(rule)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_RULE_CREATED,
            entity=AUDIT_ENTITY_RULE,
            entity_id=rule.id,
            detail={"name": rule.name, "offset_days": rule.offset_days, "template_id": template.id},
        )
        await self.db.commit()
        return rule

    async def update(self, rule_id: int, data: ScheduleRuleWrite, actor: User) -> ScheduleRule:
        rule = await self.get(rule_id)
        template = await self._require_template(data.template_id)
        if data.offset_days != rule.offset_days:
            await self._ensure_offset_available(data.offset_days, exclude_id=rule.id)

        changed: list[str] = []
        for field in ("name", "offset_days", "is_active"):
            value = getattr(data, field)
            if getattr(rule, field) != value:
                setattr(rule, field, value)
                changed.append(field)
        if rule.template_id != template.id:
            rule.template = template  # keeps the embedded template in the response in sync
            changed.append("template_id")

        rule.updated_by = actor.email
        rule.updated_at = utc_now()
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_RULE_UPDATED,
            entity=AUDIT_ENTITY_RULE,
            entity_id=rule.id,
            detail={"changed": changed},
        )
        await self.db.commit()
        return rule

    async def delete(self, rule_id: int, actor: User) -> None:
        rule = await self.get(rule_id)
        await self.rules.soft_delete(rule, actor.email)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_RULE_DELETED,
            entity=AUDIT_ENTITY_RULE,
            entity_id=rule.id,
            detail={"name": rule.name, "offset_days": rule.offset_days},
        )
        await self.db.commit()

    async def _require_template(self, template_id: int) -> MessageTemplate:
        template = await self.templates.get(template_id)
        if template is None:
            raise AppException(
                "Templat pesan tidak ditemukan.", status_code=422, code=VALIDATION_ERROR
            )
        return template

    async def _ensure_offset_available(
        self, offset_days: int, *, exclude_id: int | None = None
    ) -> None:
        # Deactivated rules still hold their offset: staff deactivate or delete to reuse it.
        if await self.rules.get_by_offset(offset_days, exclude_id=exclude_id):
            raise AppException(
                "Sudah ada aturan jadwal dengan selisih hari ini.",
                status_code=409,
                code=CONFLICT,
            )
