from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DbSession, require_permissions
from app.core.permissions import (
    SCHEDULES_CREATE,
    SCHEDULES_DELETE,
    SCHEDULES_READ,
    SCHEDULES_UPDATE,
)
from app.modules.users.model import User
from app.shared.pagination import AscListQuery, Page

from .schema import ScheduleRuleRead, ScheduleRuleWrite
from .service import ScheduleRuleService

router = APIRouter(prefix="/schedule-rules", tags=["Schedule rules"])

CanRead = Annotated[User, Depends(require_permissions(SCHEDULES_READ))]
CanCreate = Annotated[User, Depends(require_permissions(SCHEDULES_CREATE))]
CanUpdate = Annotated[User, Depends(require_permissions(SCHEDULES_UPDATE))]
CanDelete = Annotated[User, Depends(require_permissions(SCHEDULES_DELETE))]


@router.get("", response_model=Page[ScheduleRuleRead], summary="List schedule rules")
async def list_rules(
    db: DbSession, _: CanRead, params: AscListQuery, is_active: bool | None = None
) -> Page[ScheduleRuleRead]:
    rows, total = await ScheduleRuleService(db).list(params, is_active=is_active)
    return Page(
        items=[ScheduleRuleRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.post("", response_model=ScheduleRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: ScheduleRuleWrite, db: DbSession, actor: CanCreate
) -> ScheduleRuleRead:
    return ScheduleRuleRead.model_validate(await ScheduleRuleService(db).create(payload, actor))


@router.get("/{rule_id}", response_model=ScheduleRuleRead)
async def get_rule(rule_id: int, db: DbSession, _: CanRead) -> ScheduleRuleRead:
    return ScheduleRuleRead.model_validate(await ScheduleRuleService(db).get(rule_id))


@router.put("/{rule_id}", response_model=ScheduleRuleRead)
async def update_rule(
    rule_id: int, payload: ScheduleRuleWrite, db: DbSession, actor: CanUpdate
) -> ScheduleRuleRead:
    return ScheduleRuleRead.model_validate(
        await ScheduleRuleService(db).update(rule_id, payload, actor)
    )


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: int, db: DbSession, actor: CanDelete) -> Response:
    await ScheduleRuleService(db).delete(rule_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
