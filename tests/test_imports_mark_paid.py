"""Applying a rekap import marks the invoices staff selected as paid, and nothing else."""

from datetime import UTC, date, datetime
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update

from app.db.session import get_session_factory
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message

from .imports_helpers import (
    IMPORTS_URL,
    apply,
    import_details,
    import_rekap,
    invoice_by_code,
    preview_rekap,
)
from .test_students_invoices import audit_events, freeze_clock

YEAR = "2026/2027 Ganjil"
NOON_JAKARTA = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)


async def seed_three_and_preview(
    app: FastAPI, client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[dict[str, Any], dict[str, int]]:
    """Three open invoices, then a preview of a file that lacks all of them (plus one new)."""
    freeze_clock(app, NOON_JAKARTA)
    await import_rekap(
        client, headers, ("1001", YEAR, 100), ("1002", YEAR, 200), ("1003", YEAR, 300)
    )
    batch = await preview_rekap(client, headers, ("9999", YEAR, 900))
    page = await import_details(client, headers, batch["id"], "suspected_paid", page_size=100)
    return batch, {item["nim"]: item["invoice_id"] for item in page["items"]}


async def test_only_selected_suspects_become_paid(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch, ids = await seed_three_and_preview(app, client, staf_headers)

    response = await apply(client, staf_headers, batch["id"], [ids["1001"], ids["1002"]])

    assert response.status_code == 200
    assert response.json()["summary"]["invoices_paid_by_absence"] == 2
    for code in ("TGK-1001-20261", "TGK-1002-20261"):
        invoice = await invoice_by_code(code)
        assert (invoice.status, invoice.paid_at, invoice.paid_via) == (
            "paid",
            date(2026, 11, 16),
            "siakad",
        )
    assert (await invoice_by_code("TGK-1003-20261")).status == "unpaid"


async def test_apply_without_a_selection_marks_nothing(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch, _ = await seed_three_and_preview(app, client, staf_headers)

    response = await apply(client, staf_headers, batch["id"])

    assert response.json()["summary"]["invoices_paid_by_absence"] == 0
    for code in ("TGK-1001-20261", "TGK-1002-20261", "TGK-1003-20261"):
        assert (await invoice_by_code(code)).status == "unpaid"


async def test_queued_messages_of_newly_paid_invoices_are_cancelled(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch, ids = await seed_three_and_preview(app, client, staf_headers)
    invoice = await invoice_by_code("TGK-1001-20261")
    async with get_session_factory()() as db:
        db.add(
            Message(
                student_id=invoice.student_id,
                invoice_id=invoice.id,
                rule_id=None,
                kind="manual",
                scheduled_date=date(2026, 11, 17),
                body="Pengingat",
                status="queued",
            )
        )
        await db.commit()

    await apply(client, staf_headers, batch["id"], [ids["1001"]])

    async with get_session_factory()() as db:
        message = (await db.scalars(select(Message).where(Message.invoice_id == invoice.id))).one()
    assert (message.status, message.cancel_reason) == ("cancelled", "paid")


async def test_unknown_duplicate_and_non_suspect_ids_are_ignored(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    freeze_clock(app, NOON_JAKARTA)
    await import_rekap(client, staf_headers, ("1001", YEAR, 100), ("1002", YEAR, 200))
    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 100))  # 1002 suspected
    in_file_id = (await invoice_by_code("TGK-1001-20261")).id
    suspect_id = (await invoice_by_code("TGK-1002-20261")).id

    response = await apply(
        client, staf_headers, batch["id"], [suspect_id, suspect_id, in_file_id, 987654]
    )

    assert response.status_code == 200
    assert response.json()["summary"]["invoices_paid_by_absence"] == 1
    assert (await invoice_by_code("TGK-1001-20261")).status == "unpaid"
    assert (await invoice_by_code("TGK-1002-20261")).status == "paid"


async def test_invoice_paid_manually_between_preview_and_apply_is_not_counted(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch, ids = await seed_three_and_preview(app, client, staf_headers)
    async with get_session_factory()() as db:
        await db.execute(
            update(Invoice)
            .where(Invoice.id == ids["1001"])
            .values(status="paid", paid_at=date(2026, 11, 10), paid_via="sipema")
        )
        await db.commit()

    response = await apply(client, staf_headers, batch["id"], [ids["1001"], ids["1002"]])

    assert response.json()["summary"]["invoices_paid_by_absence"] == 1
    manual = await invoice_by_code("TGK-1001-20261")
    assert (manual.paid_at, manual.paid_via) == (date(2026, 11, 10), "sipema")  # untouched


async def test_second_preview_cannot_pay_the_same_invoices_twice(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first, ids = await seed_three_and_preview(app, client, staf_headers)
    second = await preview_rekap(client, staf_headers, ("9998", YEAR, 800))
    selected = [ids["1001"], ids["1002"]]

    first_result = await apply(client, staf_headers, first["id"], selected)
    second_result = await apply(client, staf_headers, second["id"], selected)

    assert first_result.json()["summary"]["invoices_paid_by_absence"] == 2
    assert second_result.json()["summary"]["invoices_paid_by_absence"] == 0


async def test_audit_event_carries_the_paid_by_absence_count(
    app: FastAPI, client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch, ids = await seed_three_and_preview(app, client, staf_headers)

    await apply(client, staf_headers, batch["id"], [ids["1003"]])

    event = (await audit_events("import.applied"))[-1]
    assert (event.detail or {})["invoices_paid_by_absence"] == 1


async def test_selecting_invoices_requires_imports_create(
    app: FastAPI,
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    pimpinan_headers: dict[str, str],
) -> None:
    batch, ids = await seed_three_and_preview(app, client, staf_headers)

    response = await apply(client, pimpinan_headers, batch["id"], [ids["1001"]])

    assert response.status_code == 403
    assert (await invoice_by_code("TGK-1001-20261")).status == "unpaid"


async def test_rollback_restores_invoices_marked_paid_by_absence(
    app: FastAPI,
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    batch, ids = await seed_three_and_preview(app, client, staf_headers)
    await apply(client, staf_headers, batch["id"], [ids["1001"]])
    assert (await invoice_by_code("TGK-1001-20261")).status == "paid"

    response = await client.post(f"{IMPORTS_URL}/{batch['id']}/rollback", headers=admin_headers)

    assert response.status_code == 200, response.text
    restored = await invoice_by_code("TGK-1001-20261")
    assert (restored.status, restored.paid_at, restored.paid_via) == ("unpaid", None, None)
