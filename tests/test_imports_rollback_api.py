"""Rollback endpoints: eligibility, conflicts, concurrency, permissions."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import AppException
from app.db.base import Base
from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch
from app.modules.imports.service import ImportService
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.users.model import User

from .imports_helpers import (
    IMPORTS_URL,
    SEMESTER,
    import_lines,
    invoice_by_code,
    line,
    make_csv,
    preview,
)
from .test_imports_rollback_core import _snapshot
from .test_planner import NOW as PLANNER_NOW
from .test_planner import TODAY as PLANNER_TODAY
from .test_planner import add_rule, all_messages, run_planner
from .test_students_invoices import INVOICES_URL, audit_events


def rollback_url(batch_id: int) -> str:
    return f"{IMPORTS_URL}/{batch_id}/rollback"


def preview_url(batch_id: int) -> str:
    return f"{IMPORTS_URL}/{batch_id}/rollback-preview"


async def rollback(client: httpx.AsyncClient, headers: dict[str, str], batch_id: int) -> Any:
    return await client.post(rollback_url(batch_id), headers=headers)


async def batch_status(client: httpx.AsyncClient, headers: dict[str, str], batch_id: int) -> str:
    response = await client.get(f"{IMPORTS_URL}/{batch_id}", headers=headers)
    return response.json()["status"]


async def blocks(client: httpx.AsyncClient, headers: dict[str, str]) -> dict[int, str | None]:
    response = await client.get(IMPORTS_URL, headers=headers)
    return {item["id"]: item["rollback_block"] for item in response.json()["items"]}


# --- access -----------------------------------------------------------------------------------


async def test_rollback_requires_login(client: httpx.AsyncClient) -> None:
    assert (await client.post(rollback_url(1))).status_code == 401
    assert (await client.get(preview_url(1))).status_code == 401


async def test_rollback_requires_the_rollback_permission(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    pimpinan_headers: dict[str, str],
) -> None:
    batch = await import_lines(client, staf_headers, line(code="INV-1"))
    for headers in (staf_headers, pimpinan_headers):
        assert (await rollback(client, headers, batch["id"])).status_code == 403
        assert (await client.get(preview_url(batch["id"]), headers=headers)).status_code == 403
    assert await batch_status(client, staf_headers, batch["id"]) == "applied"


# --- rolling back -----------------------------------------------------------------------------


async def test_rollback_latest_batch_restores_data_and_audits(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await import_lines(client, admin_headers, line(nim="2201", code="INV-1", amount="5000000"))
    before = await _snapshot()
    second = await import_lines(
        client,
        admin_headers,
        line(nim="2201", code="INV-1", amount="6000000"),
        line(nim="2202", name="Siti", code="INV-2"),
    )

    response = await rollback(client, admin_headers, second["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "rolled_back"
    assert body["rolled_back_at"] is not None
    assert await _snapshot() == before
    (event,) = await audit_events("import.rolled_back")
    assert event.entity_id == str(second["id"])
    assert event.detail == {
        "students_deleted": 1,
        "invoices_deleted": 1,
        "students_reverted": 0,
        "invoices_reverted": 1,
        "queued_messages_cancelled": 0,
    }


async def test_only_latest_applied_batch_can_be_rolled_back(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    first = await import_lines(client, admin_headers, line(nim="2201", code="INV-1"))
    second = await import_lines(client, admin_headers, line(nim="2202", name="Siti", code="INV-2"))

    blocked = await rollback(client, admin_headers, first["id"])

    assert blocked.status_code == 409
    assert blocked.json()["error"]["message"] == "Batalkan impor yang lebih baru lebih dulu."
    assert await blocks(client, admin_headers) == {first["id"]: "not_latest", second["id"]: None}

    assert (await rollback(client, admin_headers, second["id"])).status_code == 200
    assert await blocks(client, admin_headers) == {first["id"]: None, second["id"]: None}
    assert (await rollback(client, admin_headers, first["id"])).status_code == 200


async def test_legacy_batch_without_change_log_is_rejected(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    batch = await import_lines(client, admin_headers, line(code="INV-1"))
    async with get_session_factory()() as db:
        row = await db.get(ImportBatch, batch["id"])
        assert row is not None
        row.has_change_log = False
        await db.commit()

    response = await rollback(client, admin_headers, batch["id"])

    assert response.status_code == 409
    assert "sebelum fitur rollback" in response.json()["error"]["message"]
    assert await blocks(client, admin_headers) == {batch["id"]: "no_change_log"}
    assert (await client.get(preview_url(batch["id"]), headers=admin_headers)).status_code == 409


async def test_non_applied_batches_are_rejected(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    pending = await preview(client, admin_headers, line(code="INV-1"))
    discarded = await preview(client, admin_headers, line(nim="2202", name="Siti", code="INV-2"))
    await client.post(f"{IMPORTS_URL}/{discarded['id']}/discard", headers=admin_headers)
    done = await import_lines(client, admin_headers, line(nim="2203", name="Andi", code="INV-3"))
    assert (await rollback(client, admin_headers, done["id"])).status_code == 200

    for batch_id in (pending["id"], discarded["id"], done["id"]):
        response = await rollback(client, admin_headers, batch_id)
        assert response.status_code == 409, batch_id
        assert response.json()["error"]["message"] == (
            "Hanya impor berstatus Diterapkan yang dapat dibatalkan."
        )
    assert (await rollback(client, admin_headers, 99999)).status_code == 404
    assert (await client.get(preview_url(99999), headers=admin_headers)).status_code == 404


async def test_conflict_rejects_whole_rollback_and_changes_nothing(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    batch = await import_lines(
        client,
        admin_headers,
        line(nim="2201", code="INV-1"),
        line(nim="2202", name="Siti", code="INV-2"),
    )
    invoice = await invoice_by_code("INV-1")
    paid = await client.patch(
        f"{INVOICES_URL}/{invoice.id}/status", json={"status": "paid"}, headers=admin_headers
    )
    assert paid.status_code == 200
    before = await _snapshot()

    response = await rollback(client, admin_headers, batch["id"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"]["conflicts_total"] == 1
    assert error["details"]["conflicts"] == [
        {"entity": "invoice", "label": "INV-1", "fields": ["status", "paid_at"]}
    ]
    assert await batch_status(client, admin_headers, batch["id"]) == "applied"
    assert await _snapshot() == before

    previewed = await client.get(preview_url(batch["id"]), headers=admin_headers)
    assert previewed.status_code == 200
    assert previewed.json()["conflicts_total"] == 1


async def test_preview_reports_impact_without_changing_data(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    batch = await import_lines(
        client,
        admin_headers,
        line(nim="2201", code="INV-1"),
        line(nim="2202", name="Siti", code="INV-2"),
    )
    invoice = await invoice_by_code("INV-1")
    async with get_session_factory()() as db:
        for status in ("queued", "sent"):
            db.add(
                Message(
                    student_id=invoice.student_id,
                    invoice_id=invoice.id,
                    kind="manual",
                    scheduled_date=PLANNER_TODAY,
                    body="Halo",
                    status=status,
                )
            )
        await db.commit()
    before = await _snapshot()

    response = await client.get(preview_url(batch["id"]), headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {
        "students_deleted": 2,
        "invoices_deleted": 2,
        "students_reverted": 0,
        "invoices_reverted": 0,
        "queued_messages_cancelled": 1,
        "sent_messages": 1,
        "conflicts": [],
        "conflicts_total": 0,
    }
    assert await _snapshot() == before
    assert await batch_status(client, admin_headers, batch["id"]) == "applied"


async def test_import_with_no_effective_changes_can_be_rolled_back(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await import_lines(client, admin_headers, line(code="INV-1"))
    before = await _snapshot()
    same = await import_lines(client, admin_headers, line(code="INV-1"))

    response = await rollback(client, admin_headers, same["id"])

    assert response.status_code == 200
    assert await _snapshot() == before


async def test_reimport_after_rollback_restores_soft_deleted_rows(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    rows = (line(nim="2201", code="INV-1"), line(nim="2202", name="Siti", code="INV-2"))
    first = await import_lines(client, admin_headers, *rows)
    before = await _snapshot()
    assert (await rollback(client, admin_headers, first["id"])).status_code == 200
    assert await _snapshot() == {"students": {}, "invoices": {}}

    again = await import_lines(client, admin_headers, *rows)

    assert again["status"] == "applied"
    assert (await invoice_by_code("INV-1")).deleted_at is None
    assert (await _snapshot())["students"].keys() == before["students"].keys()


async def test_concurrent_rollbacks_have_exactly_one_winner(tmp_path: Path) -> None:
    """Two sessions race to roll back the same batch (own connections: see the apply race test)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    actor = User(email="admin@example.com", full_name="Admin", password_hash="x")
    try:
        async with factory() as db:
            batch = await ImportService(db).preview(
                "tagihan.csv",
                make_csv(line(code="INV-1"), line(nim="2202", name="Siti", code="INV-2")),
                source="siakad",
                semester=SEMESTER,
                actor=actor,
            )
            batch_id = batch.id
        async with factory() as db:
            await ImportService(db).apply(batch_id, actor)

        async def attempt() -> object:
            async with factory() as db:
                return await ImportService(db).rollback(batch_id, actor)

        results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

        failures = [r for r in results if isinstance(r, AppException)]
        winners = [r for r in results if isinstance(r, ImportBatch)]
        assert len(winners) == 1, results
        assert len(failures) == 1, results
        assert failures[0].status_code == 409
        async with factory() as db:
            rows = (await db.scalars(select(Invoice).where(Invoice.deleted_at.is_not(None)))).all()
            assert len(rows) == 2
    finally:
        await engine.dispose()


async def test_rolled_back_superseded_invoice_does_not_duplicate_messages(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await import_lines(client, admin_headers, line(nim="2201", code="INV-1", due="2026-11-15"))
    old = await invoice_by_code("INV-1")
    rule_id = await add_rule(0)
    async with get_session_factory()() as db:
        db.add(
            Message(
                student_id=old.student_id,
                invoice_id=old.id,
                rule_id=rule_id,
                kind="auto",
                scheduled_date=PLANNER_TODAY,
                body="x",
                status="queued",
                queued_at=PLANNER_NOW,
            )
        )
        await db.commit()
    second = await import_lines(
        client, admin_headers, line(nim="2201", code="INV-2", due="2026-12-15")
    )
    cancelled = [m for m in await all_messages() if m.invoice_id == old.id]
    assert [(m.status, m.cancel_reason) for m in cancelled] == [("cancelled", "paid")]

    assert (await rollback(client, admin_headers, second["id"])).status_code == 200
    await run_planner()

    assert (await invoice_by_code("INV-1")).status == "unpaid"
    after = [m for m in await all_messages() if m.invoice_id == old.id]
    assert [(m.status, m.cancel_reason, m.rule_id) for m in after] == [
        ("cancelled", "paid", rule_id)
    ]


# --- re-import after a rollback behaves like a first import -----------------------------------


async def test_reimport_after_rollback_supersedes_old_invoices_again(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    await import_lines(client, admin_headers, line(nim="2201", code="INV-1"))
    carrying = await import_lines(client, admin_headers, line(nim="2201", code="INV-2"))
    assert (await invoice_by_code("INV-1")).status == "paid"
    assert (await rollback(client, admin_headers, carrying["id"])).status_code == 200
    assert (await invoice_by_code("INV-1")).status == "unpaid"

    await import_lines(client, admin_headers, line(nim="2201", code="INV-2"))

    assert (await invoice_by_code("INV-1")).status == "paid"
    assert (await invoice_by_code("INV-2")).deleted_at is None


async def test_reimport_after_rollback_applies_the_corrected_paid_status(
    client: httpx.AsyncClient, admin_headers: dict[str, str]
) -> None:
    wrong = await import_lines(
        client, admin_headers, line(code="INV-5", status="lunas", paid_at="2026-11-02")
    )
    assert (await rollback(client, admin_headers, wrong["id"])).status_code == 200

    corrected = await import_lines(client, admin_headers, line(code="INV-5", status="belum lunas"))

    invoice = await invoice_by_code("INV-5")
    assert (invoice.deleted_at, invoice.status, invoice.paid_at, invoice.paid_via) == (
        None,
        "unpaid",
        None,
        None,
    )
    assert corrected["summary"]["invoices_skipped"] == 0


# --- locking ----------------------------------------------------------------------------------


async def test_apply_and_rollback_take_the_import_lock(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_lock(_db: object) -> None:
        calls.append("lock")

    monkeypatch.setattr("app.modules.imports.service.lock_imports", fake_lock)

    batch = await import_lines(client, admin_headers, line(code="INV-1"))
    assert calls == ["lock"]
    assert (await rollback(client, admin_headers, batch["id"])).status_code == 200
    assert calls == ["lock", "lock"]
