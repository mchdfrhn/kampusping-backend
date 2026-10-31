from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_permissions
from app.core.permissions import SETTINGS_READ, SETTINGS_UPDATE
from app.modules.users.model import User

from .schema import SettingsRead, SettingsWrite
from .service import SettingsService

router = APIRouter(prefix="/settings", tags=["Settings"])

CanRead = Annotated[User, Depends(require_permissions(SETTINGS_READ))]
CanUpdate = Annotated[User, Depends(require_permissions(SETTINGS_UPDATE))]


@router.get("", response_model=SettingsRead, summary="Get application settings")
async def get_settings(db: DbSession, _: CanRead) -> SettingsRead:
    row = await SettingsService(db).get()
    await db.commit()  # persists the default row when it was just created
    return SettingsRead.model_validate(row)


@router.put("", response_model=SettingsRead, summary="Update application settings")
async def update_settings(payload: SettingsWrite, db: DbSession, actor: CanUpdate) -> SettingsRead:
    return SettingsRead.model_validate(await SettingsService(db).update(payload, actor))


@router.post("/resume", response_model=SettingsRead, summary="Resume automatic sending")
async def resume_sending(db: DbSession, actor: CanUpdate) -> SettingsRead:
    return SettingsRead.model_validate(await SettingsService(db).resume(actor))
