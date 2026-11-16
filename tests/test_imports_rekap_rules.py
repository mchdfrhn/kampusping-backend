"""Rules that only apply to SIAKAD "Rekap Tunggakan" imports."""

from datetime import UTC, date, datetime

import httpx
from fastapi import FastAPI

from .imports_helpers import import_lines, import_rekap, invoice_by_code, line
from .test_students_invoices import freeze_clock

YEAR = "2026/2027 Ganjil"


async def test_rekap_new_invoice_does_not_pay_off_older_arrears(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_rekap(client, staf_headers, ("1001", "2025/2026 Genap", 900))

    second = await import_rekap(client, staf_headers, ("1001", YEAR, 100))

    assert (await invoice_by_code("TGK-1001-20252")).status == "unpaid"
    assert second["summary"]["invoices_new"] == 1
    assert second["summary"]["invoices_updated"] == 0


async def test_standard_import_still_supersedes_older_unpaid_invoices(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))

    await import_lines(client, staf_headers, line(nim="2201", code="INV-2"))

    assert (await invoice_by_code("INV-1")).status == "paid"


async def test_rekap_reimport_keeps_existing_due_date(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    freeze_clock(app, datetime(2026, 11, 16, 5, 0, tzinfo=UTC))
    await import_rekap(client, staf_headers, ("1001", YEAR, 100))

    freeze_clock(app, datetime(2026, 11, 20, 5, 0, tzinfo=UTC))
    second = await import_rekap(client, staf_headers, ("1001", YEAR, 100), ("1002", YEAR, 200))

    assert (await invoice_by_code("TGK-1001-20261")).due_date == date(2026, 11, 16)
    assert (await invoice_by_code("TGK-1002-20261")).due_date == date(2026, 11, 20)
    assert second["summary"]["invoices_unchanged"] == 1


async def test_rekap_due_date_uses_the_jakarta_date(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    freeze_clock(app, datetime(2026, 11, 16, 18, 0, tzinfo=UTC))  # already 17 Nov in Jakarta

    await import_rekap(client, staf_headers, ("1001", YEAR, 100))

    assert (await invoice_by_code("TGK-1001-20261")).due_date == date(2026, 11, 17)
