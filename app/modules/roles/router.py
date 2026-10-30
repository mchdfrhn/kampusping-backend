from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DbSession, require_permissions
from app.core.permissions import ROLES_CREATE, ROLES_DELETE, ROLES_READ, ROLES_UPDATE
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .schema import PermissionRead, RoleCreate, RoleRead, RoleUpdate
from .service import RoleService

router = APIRouter(tags=["Roles"])

CanRead = Annotated[User, Depends(require_permissions(ROLES_READ))]
CanCreate = Annotated[User, Depends(require_permissions(ROLES_CREATE))]
CanUpdate = Annotated[User, Depends(require_permissions(ROLES_UPDATE))]
CanDelete = Annotated[User, Depends(require_permissions(ROLES_DELETE))]


@router.get("/permissions", response_model=list[PermissionRead], summary="List all permissions")
async def list_permissions(db: DbSession, _: CanRead) -> list[PermissionRead]:
    rows = await RoleService(db).list_permissions()
    return [PermissionRead.model_validate(row) for row in rows]


@router.get("/roles", response_model=Page[RoleRead], summary="List roles")
async def list_roles(db: DbSession, _: CanRead, params: ListQuery) -> Page[RoleRead]:
    rows, total = await RoleService(db).list(params)
    return Page(
        items=[RoleRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.post("/roles", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(payload: RoleCreate, db: DbSession, actor: CanCreate) -> RoleRead:
    return RoleRead.model_validate(await RoleService(db).create(payload, actor))


@router.get("/roles/{role_id}", response_model=RoleRead)
async def get_role(role_id: int, db: DbSession, _: CanRead) -> RoleRead:
    return RoleRead.model_validate(await RoleService(db).get(role_id))


@router.put("/roles/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: int, payload: RoleUpdate, db: DbSession, actor: CanUpdate
) -> RoleRead:
    return RoleRead.model_validate(await RoleService(db).update(role_id, payload, actor))


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: int, db: DbSession, actor: CanDelete) -> Response:
    await RoleService(db).delete(role_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
