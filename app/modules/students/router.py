from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_permissions
from app.core.permissions import STUDENTS_READ, STUDENTS_UPDATE
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .constants import PhoneStatus
from .schema import StudentPhoneUpdate, StudentRead
from .service import StudentService

router = APIRouter(prefix="/students", tags=["Students"])

CanRead = Annotated[User, Depends(require_permissions(STUDENTS_READ))]
CanUpdate = Annotated[User, Depends(require_permissions(STUDENTS_UPDATE))]


@router.get("", response_model=Page[StudentRead], summary="List students")
async def list_students(
    db: DbSession,
    _: CanRead,
    params: ListQuery,
    study_program: Annotated[str | None, Query(max_length=150)] = None,
    cohort: Annotated[str | None, Query(max_length=20)] = None,
    phone_status: PhoneStatus | None = None,
) -> Page[StudentRead]:
    rows, total = await StudentService(db).list(
        params, study_program=study_program, cohort=cohort, phone_status=phone_status
    )
    return Page(
        items=[StudentRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{student_id}", response_model=StudentRead)
async def get_student(student_id: int, db: DbSession, _: CanRead) -> StudentRead:
    return StudentRead.model_validate(await StudentService(db).get(student_id))


@router.patch("/{student_id}", response_model=StudentRead, summary="Set or clear the phone number")
async def update_student_phone(
    student_id: int, payload: StudentPhoneUpdate, db: DbSession, actor: CanUpdate
) -> StudentRead:
    student = await StudentService(db).update_phone(student_id, payload.phone, actor)
    return StudentRead.model_validate(student)
