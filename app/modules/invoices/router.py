from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_permissions
from app.core.permissions import INVOICES_READ, INVOICES_UPDATE
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .constants import InvoiceStatus
from .schema import InvoiceRead, InvoiceStatusUpdate
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


@router.patch(
    "/{invoice_id}/status", response_model=InvoiceRead, summary="Mark an invoice paid or unpaid"
)
async def update_invoice_status(
    invoice_id: int, payload: InvoiceStatusUpdate, db: DbSession, actor: CanUpdate
) -> InvoiceRead:
    invoice = await InvoiceService(db).set_status(invoice_id, payload.status, actor)
    return InvoiceRead.model_validate(invoice)
