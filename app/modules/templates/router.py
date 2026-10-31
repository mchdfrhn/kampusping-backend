from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DbSession, require_permissions
from app.core.permissions import (
    TEMPLATES_CREATE,
    TEMPLATES_DELETE,
    TEMPLATES_READ,
    TEMPLATES_UPDATE,
)
from app.modules.users.model import User
from app.shared.pagination import AscListQuery, Page

from .schema import (
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateRead,
    TemplateWrite,
)
from .service import TemplateService

router = APIRouter(prefix="/templates", tags=["Templates"])

CanRead = Annotated[User, Depends(require_permissions(TEMPLATES_READ))]
CanCreate = Annotated[User, Depends(require_permissions(TEMPLATES_CREATE))]
CanUpdate = Annotated[User, Depends(require_permissions(TEMPLATES_UPDATE))]
CanDelete = Annotated[User, Depends(require_permissions(TEMPLATES_DELETE))]


@router.get("", response_model=Page[TemplateRead], summary="List message templates")
async def list_templates(
    db: DbSession, _: CanRead, params: AscListQuery, is_active: bool | None = None
) -> Page[TemplateRead]:
    rows, total = await TemplateService(db).list(params, is_active=is_active)
    return Page(
        items=[TemplateRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.post(
    "/preview",
    response_model=TemplatePreviewResponse,
    summary="Render a body with sample data",
)
async def preview_template(
    payload: TemplatePreviewRequest, db: DbSession, _: CanRead
) -> TemplatePreviewResponse:
    return TemplateService(db).preview(payload.body)


@router.post("", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateWrite, db: DbSession, actor: CanCreate) -> TemplateRead:
    return TemplateRead.model_validate(await TemplateService(db).create(payload, actor))


@router.get("/{template_id}", response_model=TemplateRead)
async def get_template(template_id: int, db: DbSession, _: CanRead) -> TemplateRead:
    return TemplateRead.model_validate(await TemplateService(db).get(template_id))


@router.put("/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: int, payload: TemplateWrite, db: DbSession, actor: CanUpdate
) -> TemplateRead:
    return TemplateRead.model_validate(
        await TemplateService(db).update(template_id, payload, actor)
    )


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, db: DbSession, actor: CanDelete) -> Response:
    await TemplateService(db).delete(template_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
