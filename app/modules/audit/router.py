from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_permissions
from app.core.permissions import AUDIT_READ
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .schema import AuditEventRead
from .service import AuditService

router = APIRouter(tags=["Audit"])

CanRead = Annotated[User, Depends(require_permissions(AUDIT_READ))]


@router.get("/audit-events", response_model=Page[AuditEventRead], summary="List audit events")
async def list_audit_events(
    db: DbSession,
    _: CanRead,
    params: ListQuery,
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[AuditEventRead]:
    rows, total = await AuditService(db).list(params, action=action, entity=entity)
    return Page(
        items=[AuditEventRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )
