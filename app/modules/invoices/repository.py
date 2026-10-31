from collections.abc import Sequence
from datetime import date

from sqlalchemy import or_

from app.modules.students.model import Student
from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .constants import InvoiceStatus
from .model import Invoice


class InvoiceRepository(BaseRepository[Invoice]):
    model = Invoice
    sort_fields = {
        "invoice_code": Invoice.invoice_code,
        "amount": Invoice.amount,
        "due_date": Invoice.due_date,
        "status": Invoice.status,
        "created_at": Invoice.created_at,
        "updated_at": Invoice.updated_at,
    }
    default_sort = "due_date"

    async def search(
        self,
        params: ListParams,
        *,
        status: InvoiceStatus | None = None,
        semester: str | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        student_id: int | None = None,
    ) -> tuple[Sequence[Invoice], int]:
        stmt = self.select()
        if params.search:
            pattern = like_pattern(params.search)
            # Join only for matching; the student itself is loaded by the relationship.
            stmt = stmt.join(Student, Student.id == Invoice.student_id).where(
                or_(
                    Invoice.invoice_code.ilike(pattern, escape="\\"),
                    Student.nim.ilike(pattern, escape="\\"),
                    Student.name.ilike(pattern, escape="\\"),
                )
            )
        if status:
            stmt = stmt.where(Invoice.status == status.value)
        if semester:
            stmt = stmt.where(Invoice.semester == semester)
        if due_from:
            stmt = stmt.where(Invoice.due_date >= due_from)
        if due_to:
            stmt = stmt.where(Invoice.due_date <= due_to)
        if student_id is not None:
            stmt = stmt.where(Invoice.student_id == student_id)
        return await self.paginate(stmt, params)
