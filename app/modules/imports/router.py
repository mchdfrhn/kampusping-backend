import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.core.exceptions import AppException
from app.core.permissions import IMPORTS_CREATE, IMPORTS_READ, IMPORTS_ROLLBACK
from app.modules.invoices.constants import InvoiceSource
from app.modules.users.model import User
from app.shared.pagination import ListParams, ListQuery, Page, SortOrder

from .parser import MAX_FILE_BYTES
from .schema import ImportBatchRead, ImportRowErrorRead, RollbackPreview
from .service import ImportService
from .validation import SEMESTER_MAX

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["Imports"])

CanRead = Annotated[User, Depends(require_permissions(IMPORTS_READ))]
CanCreate = Annotated[User, Depends(require_permissions(IMPORTS_CREATE))]
CanRollback = Annotated[User, Depends(require_permissions(IMPORTS_ROLLBACK))]


def get_error_list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    sort_by: Annotated[str | None, Query(max_length=50)] = None,
    sort_order: SortOrder = SortOrder.ASC,
) -> ListParams:
    """Like ``ListQuery`` but ascending by default: row issues read top to bottom."""
    return ListParams(page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order)


@router.post(
    "",
    response_model=ImportBatchRead,
    status_code=201,
    summary="Upload a SIAKAD/SIPEMA file and preview the import",
)
async def create_import(
    db: DbSession,
    actor: CanCreate,
    clock: ClockDep,
    file: Annotated[UploadFile, File(description=".csv or .xlsx, at most 5 MB")],
    source: Annotated[InvoiceSource, Form()],
    semester: Annotated[
        str | None, Form(max_length=SEMESTER_MAX, description="Default for rows without one")
    ] = None,
) -> ImportBatchRead:
    # One byte more than the limit is enough for the parser to reject an oversized file.
    content = await file.read(MAX_FILE_BYTES + 1)
    batch = await ImportService(db, clock).preview(
        file.filename or "",
        content,
        source=source.value,
        semester=(semester or "").strip() or None,
        actor=actor,
    )
    return ImportBatchRead.model_validate(batch)


@router.post(
    "/{batch_id}/apply", response_model=ImportBatchRead, summary="Apply a previewed import"
)
async def apply_import(
    batch_id: int, db: DbSession, actor: CanCreate, clock: ClockDep
) -> ImportBatchRead:
    return ImportBatchRead.model_validate(await ImportService(db, clock).apply(batch_id, actor))


@router.post(
    "/{batch_id}/discard", response_model=ImportBatchRead, summary="Discard a previewed import"
)
async def discard_import(
    batch_id: int, db: DbSession, actor: CanCreate, clock: ClockDep
) -> ImportBatchRead:
    return ImportBatchRead.model_validate(await ImportService(db, clock).discard(batch_id, actor))


@router.delete(
    "/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an import from history",
)
async def delete_import(
    batch_id: int,
    db: DbSession,
    actor: CanCreate,
    force: Annotated[bool, Query(description="Force delete an applied batch")] = False,
) -> Response:
    try:
        await ImportService(db).delete(batch_id, actor, force=force)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AppException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error deleting import batch %s: %s", batch_id, exc)
        raise


@router.post(
    "/{batch_id}/rollback",
    response_model=ImportBatchRead,
    summary="Roll back an applied import (only the latest one)",
)
async def rollback_import(
    batch_id: int, db: DbSession, actor: CanRollback, clock: ClockDep
) -> ImportBatchRead:
    return ImportBatchRead.model_validate(await ImportService(db, clock).rollback(batch_id, actor))


@router.get(
    "/{batch_id}/rollback-preview",
    response_model=RollbackPreview,
    summary="What rolling back an import would do, and what blocks it",
)
async def rollback_preview(batch_id: int, db: DbSession, _: CanRollback) -> RollbackPreview:
    return await ImportService(db).rollback_preview(batch_id)


@router.get("", response_model=Page[ImportBatchRead], summary="List import history")
async def list_imports(db: DbSession, _: CanRead, params: ListQuery) -> Page[ImportBatchRead]:
    service = ImportService(db)
    rows, total = await service.list(params)
    return Page(
        items=await service.read_models(rows),
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{batch_id}", response_model=ImportBatchRead, summary="Get one import")
async def get_import(batch_id: int, db: DbSession, _: CanRead) -> ImportBatchRead:
    service = ImportService(db)
    return (await service.read_models([await service.get(batch_id)]))[0]


@router.get(
    "/{batch_id}/errors", response_model=Page[ImportRowErrorRead], summary="List row issues"
)
async def list_import_errors(
    batch_id: int,
    db: DbSession,
    _: CanRead,
    params: Annotated[ListParams, Depends(get_error_list_params)],
) -> Page[ImportRowErrorRead]:
    rows, total = await ImportService(db).list_errors(batch_id, params)
    return Page(
        items=[ImportRowErrorRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )
