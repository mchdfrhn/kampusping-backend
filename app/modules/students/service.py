from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.users.model import User
from app.shared.pagination import ListParams
from app.shared.phone import mask_phone, normalize_phone
from app.shared.utils import utc_now

from .constants import (
    AUDIT_ENTITY_STUDENT,
    AUDIT_STUDENT_PHONE_CHANGED,
    PHONE_AUDIT_FIELD,
    PhoneStatus,
)
from .model import Student
from .repository import StudentRepository


class StudentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.students = StudentRepository(db)

    async def list(
        self,
        params: ListParams,
        *,
        study_program: str | None = None,
        cohort: str | None = None,
        phone_status: PhoneStatus | None = None,
    ) -> tuple[Sequence[Student], int]:
        return await self.students.search(
            params, study_program=study_program, cohort=cohort, phone_status=phone_status
        )

    async def get(self, student_id: int) -> Student:
        student = await self.students.get(student_id)
        if student is None:
            raise AppException("Mahasiswa tidak ditemukan.", status_code=404, code=NOT_FOUND)
        return student

    async def update_phone(self, student_id: int, raw_phone: str | None, actor: User) -> Student:
        """Set or clear the phone number. Unusable numbers are rejected and nothing changes."""
        student = await self.get(student_id)

        if raw_phone is None or not raw_phone.strip():
            new_phone = None
            new_status = PhoneStatus.MISSING
        else:
            new_phone = normalize_phone(raw_phone)
            if new_phone is None:
                raise AppException(
                    "Nomor telepon tidak valid. Gunakan nomor seluler Indonesia, "
                    "misalnya 0812-3456-7890.",
                    status_code=422,
                    code=VALIDATION_ERROR,
                )
            new_status = PhoneStatus.VALID

        old_phone = student.phone
        phone_changed = old_phone != new_phone
        if phone_changed or student.phone_status != new_status.value:
            student.phone = new_phone
            student.phone_status = new_status.value
            student.updated_by = actor.email
            student.updated_at = utc_now()

        if phone_changed:
            await record_audit(
                self.db,
                actor=actor.email,
                action=AUDIT_STUDENT_PHONE_CHANGED,
                entity=AUDIT_ENTITY_STUDENT,
                entity_id=student.id,
                # Full numbers never go into the audit trail.
                detail={
                    "field": PHONE_AUDIT_FIELD,
                    "old": mask_phone(old_phone),
                    "new": mask_phone(new_phone),
                },
            )
        await self.db.commit()
        return student
