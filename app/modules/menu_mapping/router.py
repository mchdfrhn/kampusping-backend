from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_primary_db
from app.core.auth import CurrentUser, get_current_user, require_group_admin
from app.core.constants import CONFLICT, NOT_FOUND
from app.core.exceptions import AppException

from .repository import (
    create_menu_mapping,
    delete_menu_mapping,
    get_menu_mapping_by_id,
    get_menu_mapping_by_menu_id,
    list_menu_mappings,
    update_menu_mapping,
)
from .schema import MenuMappingCreate, MenuMappingResponse, MenuMappingUpdate

router = APIRouter()


@router.get("/menus-mapping", response_model=list[MenuMappingResponse])
def get_menus_mapping(
    menu_id: int | None = Query(default=None),
    db: Session = Depends(get_primary_db),
):
    if menu_id is not None:
        row = get_menu_mapping_by_menu_id(db, menu_id)
        return [row] if row else []
    return list_menu_mappings(db)


@router.post("/menus-mapping", response_model=MenuMappingResponse, status_code=201)
def create_mapping(
    payload: MenuMappingCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    if get_menu_mapping_by_menu_id(db, payload.menu_id):
        raise AppException("Menu id already mapped", status_code=409, code=CONFLICT)
    return create_menu_mapping(db, menu_id=payload.menu_id, slug=payload.slug)


@router.put("/menus-mapping/{row_id}", response_model=MenuMappingResponse)
def update_mapping(
    row_id: int,
    payload: MenuMappingUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    row = get_menu_mapping_by_id(db, row_id)
    if not row:
        raise AppException("Menu mapping not found", status_code=404, code=NOT_FOUND)
    return update_menu_mapping(db, row, **payload.model_dump(exclude_unset=True))


@router.delete("/menus-mapping/{row_id}", status_code=204)
def remove_mapping(
    row_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_primary_db),
):
    require_group_admin(user)
    row = get_menu_mapping_by_id(db, row_id)
    if not row:
        raise AppException("Menu mapping not found", status_code=404, code=NOT_FOUND)
    delete_menu_mapping(db, row)
