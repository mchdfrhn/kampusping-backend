from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.permissions import INVOICES_READ, INVOICES_UPDATE
from app.modules.imports.parser import MAX_FILE_BYTES
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .constants import InvoiceStatus
from .schema import (
    InvoiceDeleteByCodesRequest,
    InvoiceDeleteByFilePreview,
    InvoiceDeleteByFileResult,
    InvoiceFilterOptions,
    InvoiceRead,
    InvoiceStatusUpdate,
)
from .service import InvoiceService

router = APIRouter(prefix="/invoices", tags=["Invoices"])

CanRead = Annotated[User, Depends(require_permissions(INVOICES_READ))]
CanUpdate = Annotated[User, Depends(require_permissions(INVOICES_UPDATE))]


@router.get("", response_model=Page[InvoiceRead], summary="List invoices")
async def list_invoices(
    db: DbSession,
    _: CanRead,
    params: ListQuery,
    status: InvoiceStatus | None = None,
    semester: Annotated[str | None, Query(max_length=50)] = None,
    due_from: date | None = None,
    due_to: date | None = None,
    student_id: Annotated[int | None, Query(ge=1)] = None,
) -> Page[InvoiceRead]:
    rows, total = await InvoiceService(db).list(
        params,
        status=status,
        semester=semester,
        due_from=due_from,
        due_to=due_to,
        student_id=student_id,
    )
    return Page(
        items=[InvoiceRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get(
    "/filter-options",
    response_model=InvoiceFilterOptions,
    summary="Semesters available as list filters",
)
async def get_invoice_filter_options(db: DbSession, _: CanRead) -> InvoiceFilterOptions:
    return await InvoiceService(db).filter_options()


@router.patch(
    "/{invoice_id}/status", response_model=InvoiceRead, summary="Mark an invoice paid or unpaid"
)
async def update_invoice_status(
    invoice_id: int, payload: InvoiceStatusUpdate, db: DbSession, actor: CanUpdate, clock: ClockDep
) -> InvoiceRead:
    invoice = await InvoiceService(db, clock).set_status(invoice_id, payload.status, actor)
    return InvoiceRead.model_validate(invoice)


@router.post(
    "/delete-by-file/preview",
    response_model=InvoiceDeleteByFilePreview,
    summary="Preview deletion of invoices from uploaded file",
)
async def preview_delete_invoices_by_file(
    db: DbSession,
    _: CanUpdate,
    clock: ClockDep,
    file: Annotated[UploadFile, File(description=".csv or .xlsx, at most 5 MB")],
) -> InvoiceDeleteByFilePreview:
    content = await file.read(MAX_FILE_BYTES + 1)
    return await InvoiceService(db, clock).preview_delete_by_file(file.filename or "", content)


@router.post(
    "/delete-by-file",
    response_model=InvoiceDeleteByFileResult,
    summary="Delete invoices matching uploaded file",
)
async def delete_invoices_by_file(
    db: DbSession,
    actor: CanUpdate,
    clock: ClockDep,
    file: Annotated[UploadFile, File(description=".csv or .xlsx, at most 5 MB")],
    hard_delete: Annotated[bool, Form(description="Hapus permanen dari database")] = False,
) -> InvoiceDeleteByFileResult:
    content = await file.read(MAX_FILE_BYTES + 1)
    return await InvoiceService(db, clock).delete_by_file(
        file.filename or "", content, hard_delete=hard_delete, actor=actor
    )


@router.post(
    "/delete-by-codes",
    response_model=InvoiceDeleteByFileResult,
    summary="Delete invoices matching given codes",
)
async def delete_invoices_by_codes(
    payload: InvoiceDeleteByCodesRequest,
    db: DbSession,
    actor: CanUpdate,
    clock: ClockDep,
) -> InvoiceDeleteByFileResult:
    return await InvoiceService(db, clock).delete_by_codes(
        payload.invoice_codes, hard_delete=payload.hard_delete, actor=actor
    )
