from collections.abc import Sequence

from sqlalchemy import or_

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .constants import PhoneStatus
from .model import Student


class StudentRepository(BaseRepository[Student]):
    model = Student
    sort_fields = {
        "nim": Student.nim,
        "name": Student.name,
        "study_program": Student.study_program,
        "cohort": Student.cohort,
        "created_at": Student.created_at,
        "updated_at": Student.updated_at,
    }
    default_sort = "updated_at"

    async def search(
        self,
        params: ListParams,
        *,
        study_program: str | None = None,
        cohort: str | None = None,
        phone_status: PhoneStatus | None = None,
    ) -> tuple[Sequence[Student], int]:
        stmt = self.select()
        if params.search:
            pattern = like_pattern(params.search)
            stmt = stmt.where(
                or_(
                    Student.nim.ilike(pattern, escape="\\"),
                    Student.name.ilike(pattern, escape="\\"),
                )
            )
        if study_program:
            stmt = stmt.where(Student.study_program == study_program)
        if cohort:
            stmt = stmt.where(Student.cohort == cohort)
        if phone_status:
            stmt = stmt.where(Student.phone_status == phone_status.value)
        return await self.paginate(stmt, params)
