from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import record_audit
from app.modules.users.model import User
from app.shared.clock import as_utc
from app.shared.utils import utc_now

from .constants import (
    AUDIT_ENTITY_SETTINGS,
    AUDIT_SETTINGS_RESUMED,
    AUDIT_SETTINGS_UPDATED,
    SETTINGS_ROW_ID,
)
from .model import AppSettings
from .schema import SettingsWrite, format_working_days


class SettingsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self) -> AppSettings:
        """The single settings row. Created with the defaults on first access.

        Only flushes: the CALLER commits. If another session creates the row between our check
        and our insert, the unique primary key rejects the insert; the savepoint contains that
        failure and we read the winner's row instead.
        """
        row = await self.db.get(AppSettings, SETTINGS_ROW_ID)
        if row is not None:
            return row
        row = AppSettings(id=SETTINGS_ROW_ID)  # column defaults hold the standard values
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            winner = await self.db.scalar(
                select(AppSettings)
                .where(AppSettings.id == SETTINGS_ROW_ID)
                .execution_options(populate_existing=True)
            )
            if winner is None:
                raise
            return winner
        return row

    async def update(self, data: SettingsWrite, actor: User) -> AppSettings:
        row = await self.get()
        new_values: dict[str, Any] = data.model_dump()
        new_values["working_days"] = format_working_days(data.working_days)

        changed: list[str] = []
        for field, value in new_values.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(field)

        row.updated_by = actor.email
        row.updated_at = utc_now()
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_SETTINGS_UPDATED,
            entity=AUDIT_ENTITY_SETTINGS,
            entity_id=row.id,
            detail={"changed": changed},
        )
        await self.db.commit()
        return row

    async def resume(self, actor: User) -> AppSettings:
        row = await self.get()
        previous_reason = row.paused_reason
        row.paused_reason = None
        row.consecutive_failures = 0
        row.updated_by = actor.email
        row.updated_at = utc_now()
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_SETTINGS_RESUMED,
            entity=AUDIT_ENTITY_SETTINGS,
            entity_id=row.id,
            detail={"previous_paused_reason": previous_reason},
        )
        await self.db.commit()
        return row

    async def touch_worker_heartbeat(self, *, now: datetime) -> None:
        """Record the worker's latest heartbeat without altering admin ``updated_at``."""
        await self.get()
        await self.db.execute(
            update(AppSettings)
            .where(AppSettings.id == SETTINGS_ROW_ID)
            .values(worker_last_seen_at=as_utc(now), updated_at=AppSettings.updated_at)
            .execution_options(synchronize_session=False)
        )
