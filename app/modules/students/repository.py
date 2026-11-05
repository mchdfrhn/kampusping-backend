from collections.abc import Sequence
from typing import Any

from sqlalchemy import distinct, or_, select
from sqlalchemy.orm.attributes import InstrumentedAttribute

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

    async def distinct_study_programs(self) -> list[str]:
        return await self._distinct_values(Student.study_program, Student.study_program.asc())

    async def distinct_cohorts(self) -> list[str]:
        """Newest cohort first."""
        return await self._distinct_values(Student.cohort, Student.cohort.desc())

    async def _distinct_values(
        self, column: InstrumentedAttribute[str | None], order: Any
    ) -> list[str]:
        stmt = (
            select(distinct(column))
            .where(Student.deleted_at.is_(None), column.is_not(None), column != "")
            .order_by(order)
        )
        # The WHERE clause already excludes NULLs; the filter narrows the type for the checker.
        return [value for value in await self.db.scalars(stmt) if value]
