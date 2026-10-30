from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DbSession, require_permissions
from app.core.permissions import USERS_CREATE, USERS_DELETE, USERS_READ, USERS_UPDATE
from app.shared.pagination import ListQuery, Page

from .model import User
from .schema import UserCreate, UserRead, UserUpdate
from .service import UserService

router = APIRouter(prefix="/users", tags=["Users"])

CanRead = Annotated[User, Depends(require_permissions(USERS_READ))]
CanCreate = Annotated[User, Depends(require_permissions(USERS_CREATE))]
CanUpdate = Annotated[User, Depends(require_permissions(USERS_UPDATE))]
CanDelete = Annotated[User, Depends(require_permissions(USERS_DELETE))]


@router.get("", response_model=Page[UserRead], summary="List users")
async def list_users(db: DbSession, _: CanRead, params: ListQuery) -> Page[UserRead]:
    rows, total = await UserService(db).list(params)
    return Page(
        items=[UserRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: DbSession, actor: CanCreate) -> UserRead:
    return UserRead.model_validate(await UserService(db).create(payload, actor))


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: int, db: DbSession, _: CanRead) -> UserRead:
    return UserRead.model_validate(await UserService(db).get(user_id))


@router.put("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int, payload: UserUpdate, db: DbSession, actor: CanUpdate
) -> UserRead:
    return UserRead.model_validate(await UserService(db).update(user_id, payload, actor))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: DbSession, actor: CanDelete) -> Response:
    await UserService(db).delete(user_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
