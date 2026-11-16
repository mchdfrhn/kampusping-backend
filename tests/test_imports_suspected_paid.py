"""Invoices that vanish from a rekap file are only *suspected* paid, and shown in the preview."""

from datetime import UTC, datetime

import httpx
from sqlalchemy import update

from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice

from .imports_helpers import import_details, import_rekap, preview_rekap
from .test_students_invoices import make_invoice, make_student

YEAR = "2026/2027 Ganjil"
OLD_YEAR = "2025/2026 Genap"


async def test_absent_invoice_of_a_semester_in_the_file_is_suspected(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_rekap(client, staf_headers, ("1001", YEAR, 100), ("1002", YEAR, 200))

    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 100))

    assert batch["summary"]["paid_suspected"] == 1
    page = await import_details(client, staf_headers, batch["id"], "suspected_paid")
    assert page["total"] == 1
    item = page["items"][0]
    assert item["invoice_code"] == "TGK-1002-20261"
    assert (item["nim"], item["name"], item["semester"], item["amount"]) == (
        "1002",
        "NAMA 1002",
        YEAR,
        200,
    )


async def test_semester_not_in_the_file_is_never_suspected(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_rekap(client, staf_headers, ("1001", OLD_YEAR, 900))

    batch = await preview_rekap(client, staf_headers, ("1002", YEAR, 100))

    assert batch["summary"]["paid_suspected"] == 0


async def test_non_rekap_paid_and_deleted_invoices_are_not_suspected(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    student_id = await make_student("2000", "Lama")
    await make_invoice(student_id, "INV-X", semester=YEAR)  # not a rekap code
    await make_invoice(student_id, "TGK-2000-20261", semester=YEAR, status="paid")
    deleted_id = await make_invoice(student_id, "TGK-2001-20261", semester=YEAR)
    async with get_session_factory()() as db:
        await db.execute(
            update(Invoice)
            .where(Invoice.id == deleted_id)
            .values(deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        await db.commit()

    batch = await preview_rekap(client, staf_headers, ("1002", YEAR, 100))

    assert batch["summary"]["paid_suspected"] == 0


async def test_file_without_valid_rows_yields_no_suspects(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_rekap(client, staf_headers, ("1001", YEAR, 100))

    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 0))  # amount is invalid

    assert batch["summary"]["rows_valid"] == 0
    assert batch["summary"]["paid_suspected"] == 0
    assert batch["summary"]["incomplete_warning"] == 0


async def test_incomplete_warning_boundary(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    nims = [f"{2000 + i}" for i in range(10)]
    await import_rekap(client, staf_headers, *((nim, YEAR, 100) for nim in nims))

    exactly_30_percent = await preview_rekap(
        client, staf_headers, *((nim, YEAR, 100) for nim in nims[:7])
    )
    over_30_percent = await preview_rekap(
        client, staf_headers, *((nim, YEAR, 100) for nim in nims[:6])
    )

    assert exactly_30_percent["summary"]["paid_suspected"] == 3
    assert exactly_30_percent["summary"]["incomplete_warning"] == 0
    assert over_30_percent["summary"]["paid_suspected"] == 4
    assert over_30_percent["summary"]["incomplete_warning"] == 1


async def test_empty_pool_does_not_warn(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 100))

    assert batch["summary"]["paid_suspected"] == 0
    assert batch["summary"]["incomplete_warning"] == 0


async def test_realistic_rekap_update_counts(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    current = [f"{n:04d}" for n in range(1, 227)]  # 226 open invoices of the current semester
    old_arrears = [("9001", OLD_YEAR, 500), ("9002", OLD_YEAR, 600)]
    await import_rekap(client, staf_headers, *((nim, YEAR, 100) for nim in current), *old_arrears)

    update_file = [
        *((nim, YEAR, 100) for nim in current[:150]),  # unchanged
        *((nim, YEAR, 150) for nim in current[150:203]),  # 53 amounts changed
        # current[203:] (23 students) are gone from the file: paid
        *((nim, YEAR, 700) for nim in ("9001", "9002", "9003", "9004")),  # 4 new invoices
    ]
    batch = await preview_rekap(client, staf_headers, *update_file)

    summary = batch["summary"]
    assert (
        summary["invoices_new"],
        summary["invoices_updated"],
        summary["invoices_unchanged"],
        summary["paid_suspected"],
        summary["incomplete_warning"],
    ) == (4, 53, 150, 23, 0)
    page = await import_details(client, staf_headers, batch["id"], "suspected_paid", page_size=100)
    assert page["total"] == 23
    codes = {item["invoice_code"] for item in page["items"]}
    assert "TGK-9001-20252" not in codes  # older arrears are never suspected
