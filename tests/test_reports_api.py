"""Report endpoints: preview, export, permissions and audit."""

import io
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from openpyxl import load_workbook
from sqlalchemy import select

from app.api.deps import get_clock
from app.core.permissions import REPORTS_EXPORT, REPORTS_READ
from app.db.session import get_session_factory
from app.modules.audit.model import AuditEvent
from app.modules.roles.model import Permission, Role
from app.shared.clock import Clock

from .helpers import auth_headers, create_user
from .reports_helpers import NOW, TODAY, add_invoice, add_student

OPTIONS_URL = "/api/v1/reports/options"
BILLING_URL = "/api/v1/reports/billing"
EXPORT_URL = "/api/v1/reports/billing/export"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SECRET_NIM = "NIMRAHASIA777"
SECRET_NAME = "Nama Rahasia Lengkap"
SECRET_PHONE_DIGITS = "81234567890"  # inside the fixed phone number of the test students
SECRET_CODE = "INV-RAHASIA"
PREVIEW_KEYS = {
    "preset",
    "semester",
    "period_start",
    "period_end",
    "generated_at",
    "generated_by",
    "summary",
    "groups",
    "arrears_count",
}


@pytest.fixture(autouse=True)
def frozen_clock(app: FastAPI) -> None:
    clock: Clock = lambda: NOW  # noqa: E731
    app.dependency_overrides[get_clock] = lambda: clock


async def add_overdue_invoice(**student_values: str) -> None:
    student = await add_student(
        student_values.pop("nim", SECRET_NIM), name=student_values.pop("name", SECRET_NAME)
    )
    await add_invoice(student, SECRET_CODE, due=TODAY - timedelta(days=4), amount=2_000_000)


async def headers_with_only(client: httpx.AsyncClient, *codes: str) -> dict[str, str]:
    async with get_session_factory()() as db:
        permissions = list(
            (await db.scalars(select(Permission).where(Permission.code.in_(codes)))).all()
        )
        db.add(
            Role(
                name="peran-uji",
                description="",
                is_system=False,
                permissions=permissions,
                created_by="test",
                updated_by="test",
            )
        )
        await db.commit()
    await create_user("terbatas@example.com", "peran-uji")
    return await auth_headers(client, "terbatas@example.com")


@pytest.mark.parametrize("url", [OPTIONS_URL, BILLING_URL, f"{EXPORT_URL}?format=xlsx"])
async def test_endpoints_require_authentication(client: httpx.AsyncClient, url: str) -> None:
    assert (await client.get(url)).status_code == 401


async def test_pimpinan_staf_and_admin_can_preview_and_export(
    client: httpx.AsyncClient,
    pimpinan_headers: dict[str, str],
    staf_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    for headers in (pimpinan_headers, staf_headers, admin_headers):
        assert (await client.get(OPTIONS_URL, headers=headers)).status_code == 200
        assert (await client.get(BILLING_URL, headers=headers)).status_code == 200
        export = await client.get(EXPORT_URL, params={"format": "xlsx"}, headers=headers)
        assert export.status_code == 200


async def test_export_needs_the_export_permission_and_preview_the_read_permission(
    client: httpx.AsyncClient,
) -> None:
    reader = await headers_with_only(client, REPORTS_READ)

    assert (await client.get(BILLING_URL, headers=reader)).status_code == 200
    assert (await client.get(OPTIONS_URL, headers=reader)).status_code == 200
    forbidden = await client.get(EXPORT_URL, params={"format": "xlsx"}, headers=reader)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"


async def test_a_user_with_only_export_cannot_preview(client: httpx.AsyncClient) -> None:
    exporter = await headers_with_only(client, REPORTS_EXPORT)

    assert (await client.get(BILLING_URL, headers=exporter)).status_code == 403
    assert (
        await client.get(EXPORT_URL, params={"format": "pdf"}, headers=exporter)
    ).status_code == 200


async def test_preview_has_no_personal_data(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await add_overdue_invoice()

    response = await client.get(BILLING_URL, headers=staf_headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PREVIEW_KEYS
    assert body["arrears_count"] == 1
    assert body["summary"]["invoices"]["overdue"] == 1
    assert body["groups"][0]["study_program"] == "Teknik Informatika"
    for secret in (SECRET_NIM, SECRET_NAME, SECRET_PHONE_DIGITS, SECRET_CODE):
        assert secret not in response.text


async def test_xlsx_export_headers_and_content(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await add_overdue_invoice()

    response = await client.get(
        EXPORT_URL, params={"preset": "weekly", "format": "xlsx"}, headers=staf_headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_TYPE
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="laporan-penagihan-mingguan-20261102.xlsx"'
    )
    assert response.headers["cache-control"] == "no-store"
    workbook = load_workbook(io.BytesIO(response.content))
    row = [cell.value for cell in workbook["Tunggakan"][2]]
    assert row[:2] == [SECRET_NIM, SECRET_NAME]
    every_value = " ".join(
        str(cell.value)
        for sheet in workbook.worksheets
        for line in sheet.iter_rows()
        for cell in line
        if cell.value is not None
    )
    assert SECRET_PHONE_DIGITS not in every_value


async def test_pdf_export_starts_with_the_pdf_signature(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await add_overdue_invoice()

    response = await client.get(EXPORT_URL, params={"format": "pdf"}, headers=staf_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('mingguan-20261102.pdf"')
    assert response.content.startswith(b"%PDF")


async def test_semester_filename_is_a_safe_slug(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    ok = await client.get(
        EXPORT_URL,
        params={"preset": "semester", "semester": "Ganjil 2026/2027", "format": "xlsx"},
        headers=staf_headers,
    )
    hostile = await client.get(
        EXPORT_URL,
        params={"preset": "semester", "semester": 'x"\r\nSet-Cookie: a=b', "format": "xlsx"},
        headers=staf_headers,
    )

    assert (
        ok.headers["content-disposition"]
        == 'attachment; filename="laporan-penagihan-semester-ganjil-2026-2027-20261102.xlsx"'
    )
    assert hostile.status_code == 200
    assert "set-cookie" not in hostile.headers
    assert hostile.headers["content-disposition"].startswith('attachment; filename="laporan-')
    assert hostile.headers["content-disposition"].count('"') == 2


async def test_export_writes_one_audit_event_without_personal_data(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await add_overdue_invoice()

    await client.get(EXPORT_URL, params={"format": "xlsx"}, headers=staf_headers)
    await client.get(BILLING_URL, headers=staf_headers)  # a preview is not an export

    async with get_session_factory()() as db:
        events = (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "report.exported"))
        ).all()
    assert len(events) == 1
    event = events[0]
    assert event.actor == "staf@example.com"
    assert event.entity == "report"
    assert event.detail == {
        "preset": "weekly",
        "semester": None,
        "format": "xlsx",
        "arrears_rows": 1,
    }


@pytest.mark.parametrize(
    "params",
    [
        {"preset": "semester"},
        {"preset": "bogus"},
        {"semester": "x" * 21},
    ],
)
async def test_invalid_preview_parameters_are_rejected(
    client: httpx.AsyncClient, staf_headers: dict[str, str], params: dict[str, str]
) -> None:
    assert (await client.get(BILLING_URL, params=params, headers=staf_headers)).status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"format": "xlsx", "preset": "semester"},
        {"format": "csv"},
        {"preset": "weekly"},
        {"format": "pdf", "semester": "x" * 21},
    ],
)
async def test_invalid_export_parameters_are_rejected(
    client: httpx.AsyncClient, staf_headers: dict[str, str], params: dict[str, str]
) -> None:
    response = await client.get(EXPORT_URL, params=params, headers=staf_headers)

    assert response.status_code == 422
    assert "error" in response.json()


async def test_options_lists_semesters_newest_first(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student = await add_student("2201")
    await add_invoice(student, "S1", semester="2025/2026")
    await add_invoice(student, "S2", semester="2026/2027")

    response = await client.get(OPTIONS_URL, headers=staf_headers)

    assert response.json() == {"semesters": ["2026/2027", "2025/2026"]}
