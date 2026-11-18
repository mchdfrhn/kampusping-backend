"""``GET /imports/{id}/details`` and ``/details/ids``."""

from datetime import UTC, datetime

import httpx
from sqlalchemy import update

from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch

from .imports_helpers import IMPORTS_URL, import_details, import_rekap, preview_rekap

YEAR = "2026/2027 Ganjil"


async def seeded_preview(client: httpx.AsyncClient, headers: dict[str, str]) -> dict:
    await import_rekap(
        client, headers, ("1001", YEAR, 100), ("1002", YEAR, 200), ("1003", YEAR, 300)
    )
    return await preview_rekap(
        client, headers, ("1001", YEAR, 100), ("1002", YEAR, 250), ("1004", YEAR, 400)
    )


async def test_each_category_lists_its_invoices(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await seeded_preview(client, staf_headers)

    new = await import_details(client, staf_headers, batch["id"], "new")
    changed = await import_details(client, staf_headers, batch["id"], "changed")
    suspected = await import_details(client, staf_headers, batch["id"], "suspected_paid")

    assert [item["invoice_code"] for item in new["items"]] == ["TGK-1004-20261"]
    assert [item["invoice_code"] for item in suspected["items"]] == ["TGK-1003-20261"]
    assert changed["total"] == 1
    assert changed["items"][0]["fields"] == {
        "amount": [200, 250],
        "breakdown": [
            [{"label": "002 - SPP", "amount": 200}],
            [{"label": "002 - SPP", "amount": 250}],
        ],
    }
    for item in (*new["items"], *changed["items"], *suspected["items"]):
        assert "phone" not in item


async def test_details_are_paginated(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_rekap(client, staf_headers, *((f"{n:04d}", YEAR, 100) for n in range(1, 8)))
    batch = await preview_rekap(client, staf_headers, ("9999", YEAR, 100))  # 7 suspected paid

    first = await import_details(
        client, staf_headers, batch["id"], "suspected_paid", page=1, page_size=5
    )
    second = await import_details(
        client, staf_headers, batch["id"], "suspected_paid", page=2, page_size=5
    )

    assert (first["total"], len(first["items"]), len(second["items"])) == (7, 5, 2)
    assert (first["page"], first["page_size"]) == (1, 5)


async def test_ids_endpoint_returns_every_suspected_invoice_across_pages(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    await import_rekap(client, staf_headers, *((f"{n:04d}", YEAR, 100) for n in range(1, 8)))
    batch = await preview_rekap(client, staf_headers, ("9999", YEAR, 100))

    response = await client.get(
        f"{IMPORTS_URL}/{batch['id']}/details/ids",
        params={"category": "suspected_paid"},
        headers=staf_headers,
    )

    ids = response.json()["ids"]
    assert response.status_code == 200
    page = await import_details(client, staf_headers, batch["id"], "suspected_paid", page_size=100)
    assert sorted(ids) == sorted(item["invoice_id"] for item in page["items"])


async def test_unknown_category_is_rejected(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 100))

    bad = await client.get(
        f"{IMPORTS_URL}/{batch['id']}/details", params={"category": "x"}, headers=staf_headers
    )
    ids_bad = await client.get(
        f"{IMPORTS_URL}/{batch['id']}/details/ids", params={"category": "new"}, headers=staf_headers
    )

    assert bad.status_code == 422
    assert ids_bad.status_code == 422


async def test_details_require_imports_read(
    client: httpx.AsyncClient, staf_headers: dict[str, str], pimpinan_headers: dict[str, str]
) -> None:
    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 100))
    url = f"{IMPORTS_URL}/{batch['id']}/details"

    assert (await client.get(url, params={"category": "new"})).status_code == 401
    assert (
        await client.get(url, params={"category": "new"}, headers=pimpinan_headers)
    ).status_code == 403


async def test_applied_batch_has_no_details_to_show(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    applied = await import_rekap(client, staf_headers, ("1001", YEAR, 100))

    response = await client.get(
        f"{IMPORTS_URL}/{applied['id']}/details", params={"category": "new"}, headers=staf_headers
    )

    assert response.status_code == 409


async def test_batch_previewed_before_details_existed_lists_nothing(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    batch = await preview_rekap(client, staf_headers, ("1001", YEAR, 100))
    async with get_session_factory()() as db:
        await db.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch["id"])
            .values(details=None, updated_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        await db.commit()

    page = await import_details(client, staf_headers, batch["id"], "suspected_paid")
    ids = await client.get(
        f"{IMPORTS_URL}/{batch['id']}/details/ids",
        params={"category": "suspected_paid"},
        headers=staf_headers,
    )

    assert (page["total"], page["items"]) == (0, [])
    assert ids.json() == {"ids": []}
