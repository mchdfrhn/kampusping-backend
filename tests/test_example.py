import httpx
from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.example.model import ExampleItem

Headers = dict[str, str]
URL = "/api/v1/examples"


async def _create(
    client: httpx.AsyncClient, headers: Headers, code: str, name: str, status: str = "draft"
):
    response = await client.post(
        URL, headers=headers, json={"code": code, "name": name, "status": status}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_crud_roundtrip(client: httpx.AsyncClient, admin_headers: Headers) -> None:
    item = await _create(client, admin_headers, "EX-1", "First")
    assert item["status"] == "draft"

    fetched = await client.get(f"{URL}/{item['id']}", headers=admin_headers)
    assert fetched.json()["code"] == "EX-1"

    updated = await client.put(
        f"{URL}/{item['id']}",
        headers=admin_headers,
        json={"code": "EX-1", "name": "Renamed", "status": "active"},
    )
    assert (updated.json()["name"], updated.json()["status"]) == ("Renamed", "active")

    assert (await client.delete(f"{URL}/{item['id']}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{URL}/{item['id']}", headers=admin_headers)).status_code == 404


async def test_validation_and_unique_code(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    await _create(client, admin_headers, "EX-1", "First")

    duplicate = await client.post(
        URL, headers=admin_headers, json={"code": "EX-1", "name": "Again"}
    )
    bad_status = await client.post(
        URL, headers=admin_headers, json={"code": "EX-2", "name": "x", "status": "weird"}
    )
    bad_code = await client.post(
        URL, headers=admin_headers, json={"code": "has space", "name": "x"}
    )

    assert duplicate.status_code == 409
    assert bad_status.status_code == bad_code.status_code == 422


async def test_updating_to_another_items_code_conflicts(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    await _create(client, admin_headers, "EX-1", "First")
    second = await _create(client, admin_headers, "EX-2", "Second")

    response = await client.put(
        f"{URL}/{second['id']}", headers=admin_headers, json={"code": "EX-1", "name": "Second"}
    )

    assert response.status_code == 409


async def test_delete_is_soft_and_audited(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    item = await _create(client, admin_headers, "EX-1", "First")
    await client.delete(f"{URL}/{item['id']}", headers=admin_headers)

    async with get_session_factory()() as db:
        row = await db.scalar(select(ExampleItem).where(ExampleItem.id == item["id"]))

    assert row is not None
    assert row.deleted_at is not None
    assert (row.created_by, row.deleted_by) == ("admin@example.com", "admin@example.com")
    # Soft-deleted rows disappear from listings.
    assert (await client.get(URL, headers=admin_headers)).json()["total"] == 0


async def test_list_search_filter_sort_and_paginate(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    await _create(client, admin_headers, "AAA-1", "Alpha widget", "active")
    await _create(client, admin_headers, "BBB-2", "Beta widget", "draft")
    await _create(client, admin_headers, "CCC-3", "Gamma gadget", "active")

    def query(**params: str | int):
        return client.get(URL, headers=admin_headers, params=params)

    assert (await query(search="widget")).json()["total"] == 2
    assert (await query(search="ccc")).json()["items"][0]["name"] == "Gamma gadget"
    assert (await query(status="active")).json()["total"] == 2
    assert (await query(search="100%")).json()["total"] == 0  # LIKE wildcards are escaped

    ordered = await query(sort_by="code", sort_order="asc", page_size=2)
    body = ordered.json()
    assert [item["code"] for item in body["items"]] == ["AAA-1", "BBB-2"]
    assert (body["total"], body["page"], body["page_size"]) == (3, 1, 2)

    second_page = await query(sort_by="code", sort_order="asc", page_size=2, page=2)
    assert [item["code"] for item in second_page.json()["items"]] == ["CCC-3"]

    assert (await query(sort_by="nope")).status_code == 422
    assert (await query(status="weird")).status_code == 422


async def test_date_range_filter_is_inclusive(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    await _create(client, admin_headers, "AAA-1", "Today")
    today = (await client.get(URL, headers=admin_headers)).json()["items"][0]["updated_at"][:10]

    inside = await client.get(
        URL, headers=admin_headers, params={"date_from": today, "date_to": today}
    )
    after = await client.get(URL, headers=admin_headers, params={"date_from": "2999-01-01"})
    before = await client.get(URL, headers=admin_headers, params={"date_to": "2000-01-01"})

    assert inside.json()["total"] == 1
    assert after.json()["total"] == before.json()["total"] == 0


async def test_export_csv_neutralises_formulas(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    await _create(client, admin_headers, "EX-1", '=HYPERLINK("http://evil")')

    response = await client.get(f"{URL}/export", headers=admin_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.splitlines()
    assert lines[0] == "ID,Code,Name,Status,Created at,Updated at"
    assert "'=HYPERLINK" in lines[1]
    assert ",=HYPERLINK" not in lines[1]
