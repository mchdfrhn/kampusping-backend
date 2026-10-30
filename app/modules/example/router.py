from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import DbSession, require_permissions
from app.core.permissions import (
    EXAMPLES_CREATE,
    EXAMPLES_DELETE,
    EXAMPLES_READ,
    EXAMPLES_UPDATE,
)
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .constants import ExampleStatus
from .repository import ExampleFilters
from .schema import ExampleRead, ExampleWrite
from .service import ExampleService

router = APIRouter(prefix="/examples", tags=["Examples"])

CanRead = Annotated[User, Depends(require_permissions(EXAMPLES_READ))]
CanCreate = Annotated[User, Depends(require_permissions(EXAMPLES_CREATE))]
CanUpdate = Annotated[User, Depends(require_permissions(EXAMPLES_UPDATE))]
CanDelete = Annotated[User, Depends(require_permissions(EXAMPLES_DELETE))]


def get_filters(
    status: Annotated[ExampleStatus | None, Query()] = None,
    date_from: Annotated[date | None, Query(description="Updated on/after (YYYY-MM-DD)")] = None,
    date_to: Annotated[date | None, Query(description="Updated on/before (YYYY-MM-DD)")] = None,
) -> ExampleFilters:
    return ExampleFilters(status=status, date_from=date_from, date_to=date_to)


Filters = Annotated[ExampleFilters, Depends(get_filters)]


@router.get("", response_model=Page[ExampleRead], summary="List example items")
async def list_items(
    db: DbSession, _: CanRead, params: ListQuery, filters: Filters
) -> Page[ExampleRead]:
    rows, total = await ExampleService(db).list(params, filters)
    return Page(
        items=[ExampleRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


# Declared before "/{item_id}" so "export" is never parsed as an id.
@router.get("/export", summary="Export the filtered list as CSV", response_class=Response)
async def export_items(db: DbSession, _: CanRead, params: ListQuery, filters: Filters) -> Response:
    content = await ExampleService(db).export_csv(params, filters)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="examples.csv"'},
    )


@router.post("", response_model=ExampleRead, status_code=status.HTTP_201_CREATED)
async def create_item(payload: ExampleWrite, db: DbSession, actor: CanCreate) -> ExampleRead:
    return ExampleRead.model_validate(await ExampleService(db).create(payload, actor))


@router.get("/{item_id}", response_model=ExampleRead)
async def get_item(item_id: int, db: DbSession, _: CanRead) -> ExampleRead:
    return ExampleRead.model_validate(await ExampleService(db).get(item_id))


@router.put("/{item_id}", response_model=ExampleRead)
async def update_item(
    item_id: int, payload: ExampleWrite, db: DbSession, actor: CanUpdate
) -> ExampleRead:
    return ExampleRead.model_validate(await ExampleService(db).update(item_id, payload, actor))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: int, db: DbSession, actor: CanDelete) -> Response:
    await ExampleService(db).delete(item_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
