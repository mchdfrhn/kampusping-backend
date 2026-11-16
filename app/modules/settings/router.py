from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.permissions import SETTINGS_READ, SETTINGS_UPDATE
from app.modules.users.model import User

from .schema import SettingsRead, SettingsWrite
from .service import SettingsService

router = APIRouter(prefix="/settings", tags=["Settings"])

CanRead = Annotated[User, Depends(require_permissions(SETTINGS_READ))]
CanUpdate = Annotated[User, Depends(require_permissions(SETTINGS_UPDATE))]


@router.get("", response_model=SettingsRead, summary="Get application settings")
async def get_settings(db: DbSession, _: CanRead, clock: ClockDep) -> SettingsRead:
    row = await SettingsService(db).get()
    await db.commit()  # persists the default row when it was just created
    return SettingsRead.from_settings(row, now=clock())


@router.put("", response_model=SettingsRead, summary="Update application settings")
async def update_settings(
    payload: SettingsWrite, db: DbSession, actor: CanUpdate, clock: ClockDep
) -> SettingsRead:
    return SettingsRead.from_settings(await SettingsService(db).update(payload, actor), now=clock())


@router.post("/resume", response_model=SettingsRead, summary="Resume automatic sending")
async def resume_sending(db: DbSession, actor: CanUpdate, clock: ClockDep) -> SettingsRead:
    return SettingsRead.from_settings(await SettingsService(db).resume(actor), now=clock())
