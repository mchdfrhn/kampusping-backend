from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.permissions import DASHBOARD_READ
from app.modules.users.model import User

from .constants import SEMESTER_MAX_LENGTH
from .schema import DashboardSummary
from .service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

CanRead = Annotated[User, Depends(require_permissions(DASHBOARD_READ))]


@router.get("/summary", response_model=DashboardSummary, summary="Dashboard summary")
async def dashboard_summary(
    db: DbSession,
    clock: ClockDep,
    _: CanRead,
    semester: Annotated[str | None, Query(max_length=SEMESTER_MAX_LENGTH)] = None,
) -> DashboardSummary:
    return await DashboardService(db, clock).summary(semester)
