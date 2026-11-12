import builtins
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import PureWindowsPath
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import CONFLICT, NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.users.model import User
from app.shared.clock import Clock, local_today
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .apply import apply_rows
from .changes import ChangeRecorder
from .constants import (
    AUDIT_ENTITY_IMPORT,
    AUDIT_IMPORT_APPLIED,
    AUDIT_IMPORT_DELETED,
    AUDIT_IMPORT_DISCARDED,
    AUDIT_IMPORT_PREVIEWED,
    AUDIT_IMPORT_ROLLED_BACK,
    FILE_NAME_MAX,
    MAX_REPORTED_CONFLICTS,
    MAX_STORED_ISSUES,
    MSG_BATCH_NOT_FOUND,
    MSG_BATCH_NOT_PREVIEW,
    MSG_DELETE_APPLIED_NOT_ALLOWED,
    MSG_NO_VALID_ROWS,
    MSG_ROLLBACK_CONFLICT,
    MSG_ROLLBACK_NO_LOG,
    MSG_ROLLBACK_NOT_APPLIED,
    MSG_ROLLBACK_NOT_LATEST,
    ImportStatus,
)
from .locking import lock_imports
from .model import ImportBatch, ImportRowError
from .parser import ImportFileError, parse_upload, resolve_columns
from .payload import row_from_json, row_to_json
from .preview import build_preview
from .repository import ImportBatchRepository, ImportRowErrorRepository
from .rollback import RollbackPlan, build_plan, execute_plan
from .schema import ImportBatchRead, RollbackConflictRead, RollbackPreview


def safe_file_name(filename: str) -> str:
    """Base name only (either path separator), at most ``FILE_NAME_MAX`` characters."""
    return PureWindowsPath(filename.strip()).name[:FILE_NAME_MAX] or "berkas"


def _not_in_preview() -> AppException:
    return AppException(MSG_BATCH_NOT_PREVIEW, status_code=409, code=CONFLICT)


def _conflict(message: str, details: object = None) -> AppException:
    return AppException(message, status_code=409, code=CONFLICT, details=details)


def _rollback_block(
    batch: ImportBatch, latest_applied_id: int | None
) -> Literal["no_change_log", "not_latest"] | None:
    """Why ``batch`` cannot be rolled back right now (``None``: it can, or it is not applied)."""
    if batch.status != ImportStatus.APPLIED.value:
        return None
    if not batch.has_change_log:
        return "no_change_log"
    if batch.id != latest_applied_id:
        return "not_latest"
    return None


def _impact(plan: RollbackPlan) -> dict[str, int]:
    return {
        "students_deleted": plan.students_deleted,
        "invoices_deleted": plan.invoices_deleted,
        "students_reverted": plan.students_reverted,
        "invoices_reverted": plan.invoices_reverted,
        "queued_messages_cancelled": plan.queued_messages,
    }


class ImportService:
    def __init__(self, db: AsyncSession, clock: Clock = utc_now) -> None:
        self.db = db
        self.clock = clock
        self.batches = ImportBatchRepository(db)
        self.row_errors = ImportRowErrorRepository(db)

    async def list(self, params: ListParams) -> tuple[Sequence[ImportBatch], int]:
        return await self.batches.search(params)

    async def get(self, batch_id: int) -> ImportBatch:
        batch = await self.batches.get(batch_id)
        if batch is None:
            raise AppException(MSG_BATCH_NOT_FOUND, status_code=404, code=NOT_FOUND)
        return batch

    async def list_errors(
        self, batch_id: int, params: ListParams
    ) -> tuple[Sequence[ImportRowError], int]:
        await self.get(batch_id)
        return await self.row_errors.search_for_batch(batch_id, params)

    async def preview(
        self, filename: str, content: bytes, *, source: str, semester: str | None, actor: User
    ) -> ImportBatch:
        """Parse and validate a file and store the result as a ``preview`` batch.

        Business data (students, invoices) is not touched.
        """
        try:
            sheet = parse_upload(filename, content)
            columns = resolve_columns(sheet.headers, source)
        except ImportFileError as exc:
            raise AppException(str(exc), status_code=422, code=VALIDATION_ERROR) from exc

        result = await build_preview(self.db, sheet, columns, source=source, semester=semester)
        batch = await self.batches.add(
            ImportBatch(
                source=source,
                file_name=safe_file_name(filename),
                semester=semester,
                status=ImportStatus.PREVIEW.value,
                summary=result.summary,
                rows=[row_to_json(row) for row in result.rows],
                created_by=actor.email,
                updated_by=actor.email,
            )
        )
        self.db.add_all(
            ImportRowError(
                batch_id=batch.id,
                row_number=issue.row_number,
                field=issue.field,
                message=issue.message,
                created_by=actor.email,
                updated_by=actor.email,
            )
            for issue in result.issues[:MAX_STORED_ISSUES]
        )
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_IMPORT_PREVIEWED,
            entity=AUDIT_ENTITY_IMPORT,
            entity_id=batch.id,
            detail={**result.summary},
        )
        await self.db.commit()
        return batch

    async def apply(self, batch_id: int, actor: User) -> ImportBatch:
        """Apply a previewed batch in one transaction; the summary becomes the actual counts.

        The batch is claimed first (``UPDATE ... WHERE status = 'preview'``), so of several
        simultaneous applies exactly one proceeds and the rest get 409. Any failure afterwards
        rolls the claim back together with the data changes.
        """
        await lock_imports(self.db)
        existing = await self.get(batch_id)
        if existing.status == ImportStatus.PREVIEW.value and not existing.summary.get("rows_valid"):
            raise AppException(MSG_NO_VALID_ROWS, status_code=422, code=VALIDATION_ERROR)
        now = self.clock()
        claimed = await self.batches.claim_preview(
            batch_id,
            {
                "status": ImportStatus.APPLIED.value,
                "applied_at": now,
                "updated_by": actor.email,
                "updated_at": now,
            },
        )
        if not claimed:
            raise _not_in_preview()

        batch = await self.batches.get_with_rows(batch_id)
        if batch is None:  # deleted between the claim and the read: nothing sensible to apply
            raise AppException(MSG_BATCH_NOT_FOUND, status_code=404, code=NOT_FOUND)
        rows = [row_from_json(data) for data in batch.rows or []]
        recorder = ChangeRecorder()
        counts = await apply_rows(
            self.db,
            rows,
            source=batch.source,
            actor=actor.email,
            now=now,
            today=local_today(now, settings.app_timezone),
            recorder=recorder,
        )
        self.db.add_all(recorder.build(batch_id=batch.id, actor=actor.email, now=now))
        batch.has_change_log = True
        batch.summary = {**batch.summary, **counts}
        batch.rows = None
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_IMPORT_APPLIED,
            entity=AUDIT_ENTITY_IMPORT,
            entity_id=batch.id,
            detail={**batch.summary},
        )
        await self.db.commit()
        return batch

    async def discard(self, batch_id: int, actor: User) -> ImportBatch:
        batch = await self.get(batch_id)
        now = self.clock()
        claimed = await self.batches.claim_preview(
            batch_id,
            {
                "status": ImportStatus.DISCARDED.value,
                "rows": None,
                "updated_by": actor.email,
                "updated_at": now,
            },
        )
        if not claimed:
            raise _not_in_preview()
        await self.db.refresh(batch)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_IMPORT_DISCARDED,
            entity=AUDIT_ENTITY_IMPORT,
            entity_id=batch.id,
            detail={**batch.summary},
        )
        await self.db.commit()
        return batch

    async def delete(self, batch_id: int, actor: User) -> None:
        """Delete a non-applied (preview, discarded, or rolled-back) import from history."""
        batch = await self.get(batch_id)
        if batch.status == ImportStatus.APPLIED.value:
            raise _conflict(MSG_DELETE_APPLIED_NOT_ALLOWED)
        batch.rows = None
        await self.batches.soft_delete(batch, actor.email)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_IMPORT_DELETED,
            entity=AUDIT_ENTITY_IMPORT,
            entity_id=batch.id,
            detail={"file_name": batch.file_name, "status": batch.status},
        )
        await self.db.commit()

    async def read_models(self, batches: Sequence[ImportBatch]) -> builtins.list[ImportBatchRead]:
        """Read schemas with ``rollback_block`` filled (one extra query for the whole page)."""
        latest = await self.batches.latest_applied_id()
        items: builtins.list[ImportBatchRead] = []
        for batch in batches:
            item = ImportBatchRead.model_validate(batch)
            item.rollback_block = _rollback_block(batch, latest)
            items.append(item)
        return items

    async def _rollbackable(self, batch_id: int) -> ImportBatch:
        batch = await self.get(batch_id)
        if batch.status != ImportStatus.APPLIED.value:
            raise _conflict(MSG_ROLLBACK_NOT_APPLIED)
        block = _rollback_block(batch, await self.batches.latest_applied_id())
        if block == "no_change_log":
            raise _conflict(MSG_ROLLBACK_NO_LOG)
        if block == "not_latest":
            raise _conflict(MSG_ROLLBACK_NOT_LATEST)
        return batch

    async def rollback_preview(self, batch_id: int) -> RollbackPreview:
        """What a rollback would do and what blocks it; changes nothing."""
        await self._rollbackable(batch_id)
        plan = await build_plan(self.db, batch_id)
        return RollbackPreview(
            **_impact(plan),
            sent_messages=plan.sent_messages,
            conflicts=[
                RollbackConflictRead(**asdict(c)) for c in plan.conflicts[:MAX_REPORTED_CONFLICTS]
            ],
            conflicts_total=len(plan.conflicts),
        )

    async def rollback(self, batch_id: int, actor: User) -> ImportBatch:
        """Undo an applied import in one transaction (see ``rollback.py``).

        Like ``apply``, the batch is claimed first (``UPDATE ... WHERE status = 'applied'``) so of
        several simultaneous rollbacks exactly one proceeds. Any conflict or failure afterwards
        rolls the claim back together with the data changes: nothing is applied partially.
        """
        await lock_imports(self.db)  # after this no apply is mid-flight, so "latest" is reliable
        batch = await self._rollbackable(batch_id)
        now = self.clock()
        claimed = await self.batches.claim_applied(
            batch_id,
            {
                "status": ImportStatus.ROLLED_BACK.value,
                "rolled_back_at": now,
                "updated_by": actor.email,
                "updated_at": now,
            },
        )
        if not claimed:
            raise _conflict(MSG_ROLLBACK_NOT_APPLIED)

        plan = await build_plan(self.db, batch_id, lock=True)
        if plan.conflicts:
            await self.db.rollback()  # releases the claim
            raise _conflict(
                MSG_ROLLBACK_CONFLICT,
                {
                    "conflicts": [asdict(c) for c in plan.conflicts[:MAX_REPORTED_CONFLICTS]],
                    "conflicts_total": len(plan.conflicts),
                },
            )
        await execute_plan(self.db, plan, actor=actor.email, now=now)
        await self.db.refresh(batch)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_IMPORT_ROLLED_BACK,
            entity=AUDIT_ENTITY_IMPORT,
            entity_id=batch.id,
            detail=_impact(plan),
        )
        await self.db.commit()
        return batch
