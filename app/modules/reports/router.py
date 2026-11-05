import asyncio
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.config import settings
from app.core.permissions import REPORTS_EXPORT, REPORTS_READ
from app.modules.audit.service import record_audit
from app.modules.dashboard.constants import SEMESTER_MAX_LENGTH
from app.modules.users.model import User
from app.shared.clock import local_today

from .constants import (
    AUDIT_ENTITY_REPORT,
    AUDIT_REPORT_EXPORTED,
    ReportFormat,
    ReportPreset,
)
from .pdf_builder import build_pdf
from .schema import ReportData, ReportOptions, ReportPreview
from .service import ReportService
from .xlsx_builder import build_xlsx

router = APIRouter(prefix="/reports", tags=["Reports"])

CanRead = Annotated[User, Depends(require_permissions(REPORTS_READ))]
CanExport = Annotated[User, Depends(require_permissions(REPORTS_EXPORT))]

PresetQuery = Annotated[ReportPreset, Query(description="`weekly` or `semester`")]
SemesterQuery = Annotated[str | None, Query(max_length=SEMESTER_MAX_LENGTH)]

MEDIA_TYPES = {
    ReportFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ReportFormat.PDF: "application/pdf",
}


def report_filename(data: ReportData, file_format: ReportFormat) -> str:
    """``laporan-penagihan-mingguan-20261102.xlsx``; the semester is reduced to ``[a-z0-9-]``."""
    if data.semester is None:
        scope = "mingguan"
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", data.semester.lower()).strip("-")
        scope = f"semester-{slug}" if slug else "semester"
    day = local_today(data.generated_at, settings.app_timezone)
    return f"laporan-penagihan-{scope}-{day:%Y%m%d}.{file_format.value}"


@router.get("/options", response_model=ReportOptions, summary="Semesters a report can cover")
async def report_options(db: DbSession, clock: ClockDep, _: CanRead) -> ReportOptions:
    return ReportOptions(semesters=await ReportService(db, clock).semesters())


@router.get("/billing", response_model=ReportPreview, summary="Billing report preview")
async def billing_report(
    db: DbSession,
    clock: ClockDep,
    user: CanRead,
    preset: PresetQuery = ReportPreset.WEEKLY,
    semester: SemesterQuery = None,
) -> ReportPreview:
    data = await ReportService(db, clock).build(preset, semester, user.email)
    return data.to_preview()


@router.get(
    "/billing/export",
    summary="Download the billing report as Excel or PDF",
    response_class=Response,
    responses={200: {"content": {media: {} for media in MEDIA_TYPES.values()}}},
)
async def export_billing_report(
    db: DbSession,
    clock: ClockDep,
    user: CanExport,
    file_format: Annotated[ReportFormat, Query(alias="format")],
    preset: PresetQuery = ReportPreset.WEEKLY,
    semester: SemesterQuery = None,
) -> Response:
    data = await ReportService(db, clock).build(preset, semester, user.email)
    builder = build_xlsx if file_format is ReportFormat.XLSX else build_pdf
    content = await asyncio.to_thread(builder, data)
    await record_audit(
        db,
        actor=user.email,
        action=AUDIT_REPORT_EXPORTED,
        entity=AUDIT_ENTITY_REPORT,
        detail={
            "preset": data.preset.value,
            "semester": data.semester,
            "format": file_format.value,
            "arrears_rows": len(data.arrears),
        },
    )
    await db.commit()
    return Response(
        content=content,
        media_type=MEDIA_TYPES[file_format],
        headers={
            "Content-Disposition": f'attachment; filename="{report_filename(data, file_format)}"',
            "Cache-Control": "no-store",
        },
    )
