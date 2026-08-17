from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_primary_db
from app.core.auth import CurrentUser, get_current_user, require_group_admin
from app.core.constants import CONFLICT, NOT_FOUND
from app.core.exceptions import AppException
from app.core.role_catalog import ROLE_TYPE_ADMIN, ROLE_TYPE_DEALER, ROLE_TYPE_EMPLOYEE, ROLE_TYPE_SUPER_ADMIN

from .repository import (
    create_app_allowed_role,
    delete_app_allowed_role,
    get_app_allowed_role,
    get_app_allowed_role_by_iam_id,
    list_app_allowed_roles,
    update_app_allowed_role,
)
from .schema import AppAllowedRoleCreate, AppAllowedRoleResponse, AppAllowedRoleUpdate

router = APIRouter()

ALLOWED_ROLE_TYPES = {
    ROLE_TYPE_SUPER_ADMIN,
    ROLE_TYPE_ADMIN,
    ROLE_TYPE_DEALER,
    ROLE_TYPE_EMPLOYEE,
}


@router.get("/app-roles", response_model=list[AppAllowedRoleResponse])
def get_app_roles(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    return list_app_allowed_roles(db)


@router.post("/app-roles", response_model=AppAllowedRoleResponse, status_code=201)
def create_app_role(
    payload: AppAllowedRoleCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    if payload.role_type not in ALLOWED_ROLE_TYPES:
        raise AppException("Invalid role_type. Use values from role_catalog.", code="invalid_role_type")
    if get_app_allowed_role_by_iam_id(db, payload.iam_role_id):
        raise AppException("IAM role id already mapped", status_code=409, code=CONFLICT)
    return create_app_allowed_role(
        db,
        iam_role_id=payload.iam_role_id,
        display_name=payload.display_name,
        auth_slug=payload.auth_slug,
        role_type=payload.role_type,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )


@router.put("/app-roles/{row_id}", response_model=AppAllowedRoleResponse)
def update_app_role(
    row_id: int,
    payload: AppAllowedRoleUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    row = get_app_allowed_role(db, row_id)
    if not row:
        raise AppException("App role mapping not found", status_code=404, code=NOT_FOUND)
    if payload.role_type is not None and payload.role_type not in ALLOWED_ROLE_TYPES:
        raise AppException("Invalid role_type. Use values from role_catalog.", code="invalid_role_type")
    if payload.iam_role_id is not None and payload.iam_role_id != row.iam_role_id:
        existing = get_app_allowed_role_by_iam_id(db, payload.iam_role_id)
        if existing and existing.id != row.id:
            raise AppException("IAM role id already mapped", status_code=409, code=CONFLICT)
    return update_app_allowed_role(db, row, **payload.model_dump(exclude_unset=True))


@router.delete("/app-roles/{row_id}", status_code=204)
def remove_app_role(
    row_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    row = get_app_allowed_role(db, row_id)
    if not row:
        raise AppException("App role mapping not found", status_code=404, code=NOT_FOUND)
    delete_app_allowed_role(db, row)
