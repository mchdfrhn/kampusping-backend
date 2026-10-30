import httpx
import pytest
from sqlalchemy import select

from app.core.permissions import ADMIN_ROLE, ALL_PERMISSION_CODES, MEMBER_ROLE
from app.db.session import get_session_factory
from app.modules.users.model import User

from .helpers import PASSWORD, auth_headers, create_user, login

Headers = dict[str, str]


# --- Authorization ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/roles"),
        ("GET", "/api/v1/permissions"),
        ("GET", "/api/v1/examples"),
        ("POST", "/api/v1/examples"),
    ],
)
async def test_endpoints_require_authentication(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)

    assert response.status_code == 401


async def test_member_is_forbidden_from_admin_endpoints(
    client: httpx.AsyncClient, member_headers: Headers
) -> None:
    users = await client.get("/api/v1/users", headers=member_headers)
    roles = await client.get("/api/v1/roles", headers=member_headers)
    write = await client.post(
        "/api/v1/examples", headers=member_headers, json={"code": "A1", "name": "x"}
    )

    assert users.status_code == roles.status_code == write.status_code == 403
    assert users.json()["error"]["code"] == "forbidden"
    assert users.json()["error"]["details"] == {"required": ["users:read"]}
    # ...but the permission they do hold works.
    assert (await client.get("/api/v1/examples", headers=member_headers)).status_code == 200


async def test_deactivated_user_loses_access_immediately(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    member = await create_user("member@example.com", MEMBER_ROLE)
    member_headers = await auth_headers(client, "member@example.com")
    assert (await client.get("/api/v1/examples", headers=member_headers)).status_code == 200

    await client.put(
        f"/api/v1/users/{member.id}",
        headers=admin_headers,
        json={"full_name": "Member", "is_active": False, "role_ids": []},
    )

    assert (await client.get("/api/v1/examples", headers=member_headers)).status_code == 401
    assert (await login(client, "member@example.com")).status_code == 401


# --- Users --------------------------------------------------------------------------------


async def _role_id(client: httpx.AsyncClient, headers: Headers, name: str) -> int:
    page = (await client.get("/api/v1/roles", headers=headers)).json()
    return next(role["id"] for role in page["items"] if role["name"] == name)


async def test_admin_manages_users(client: httpx.AsyncClient, admin_headers: Headers) -> None:
    member_role = await _role_id(client, admin_headers, MEMBER_ROLE)

    created = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "New.User@Example.com",
            "full_name": "New User",
            "password": PASSWORD,
            "role_ids": [member_role],
        },
    )
    assert created.status_code == 201
    user = created.json()
    assert user["email"] == "new.user@example.com"
    assert [role["name"] for role in user["roles"]] == [MEMBER_ROLE]
    assert "password" not in created.text

    assert (await login(client, "new.user@example.com")).status_code == 200

    updated = await client.put(
        f"/api/v1/users/{user['id']}",
        headers=admin_headers,
        json={"full_name": "Renamed", "is_active": True, "role_ids": []},
    )
    assert updated.json()["full_name"] == "Renamed"
    assert updated.json()["roles"] == []

    listing = await client.get("/api/v1/users", headers=admin_headers, params={"search": "renam"})
    assert [item["email"] for item in listing.json()["items"]] == ["new.user@example.com"]


async def test_duplicate_email_is_rejected(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    payload = {"email": "admin@example.com", "full_name": "Dup", "password": PASSWORD}

    response = await client.post("/api/v1/users", headers=admin_headers, json=payload)

    assert response.status_code == 409


async def test_user_creation_validates_password_and_roles(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    base = {"email": "x@example.com", "full_name": "X"}

    weak = await client.post(
        "/api/v1/users", headers=admin_headers, json={**base, "password": "weak"}
    )
    bad_role = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={**base, "password": PASSWORD, "role_ids": [9999]},
    )

    assert weak.status_code == 422
    assert bad_role.status_code == 422


async def test_delete_is_soft_and_revokes_sessions(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    member = await create_user("member@example.com", MEMBER_ROLE)
    await login(client, "member@example.com")

    response = await client.delete(f"/api/v1/users/{member.id}", headers=admin_headers)

    assert response.status_code == 204
    assert (
        await client.get(f"/api/v1/users/{member.id}", headers=admin_headers)
    ).status_code == 404
    async with get_session_factory()() as db:
        row = await db.scalar(select(User).where(User.id == member.id))
    assert row is not None
    assert row.deleted_at is not None
    assert row.deleted_by == "admin@example.com"


async def test_admin_cannot_delete_or_deactivate_self(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()

    delete = await client.delete(f"/api/v1/users/{me['id']}", headers=admin_headers)
    deactivate = await client.put(
        f"/api/v1/users/{me['id']}",
        headers=admin_headers,
        json={"full_name": "Admin", "is_active": False, "role_ids": []},
    )

    assert delete.status_code == deactivate.status_code == 409


async def test_list_contract_pagination_and_sorting(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    for index in range(5):
        await create_user(f"user{index}@example.com", MEMBER_ROLE)

    page = await client.get(
        "/api/v1/users",
        headers=admin_headers,
        params={"page": 2, "page_size": 2, "sort_by": "email", "sort_order": "asc"},
    )
    body = page.json()

    assert body["total"] == 6
    assert (body["page"], body["page_size"]) == (2, 2)
    # Ascending: admin, user0 | user1, user2 | user3, user4
    assert [item["email"] for item in body["items"]] == ["user1@example.com", "user2@example.com"]

    assert (
        await client.get(
            "/api/v1/users", headers=admin_headers, params={"sort_by": "password_hash"}
        )
    ).status_code == 422
    assert (
        await client.get("/api/v1/users", headers=admin_headers, params={"page_size": 1000})
    ).status_code == 422


# --- Roles --------------------------------------------------------------------------------


async def test_permissions_catalog_is_seeded(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    response = await client.get("/api/v1/permissions", headers=admin_headers)

    assert [item["code"] for item in response.json()] == sorted(ALL_PERMISSION_CODES)


async def test_role_crud_with_permissions(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    created = await client.post(
        "/api/v1/roles",
        headers=admin_headers,
        json={"name": "Reviewer", "description": "Read only", "permissions": ["examples:read"]},
    )
    assert created.status_code == 201
    role = created.json()
    assert role["permissions"] == ["examples:read"]
    assert role["is_system"] is False

    updated = await client.put(
        f"/api/v1/roles/{role['id']}",
        headers=admin_headers,
        json={"name": "Reviewer", "permissions": ["examples:read", "examples:update"]},
    )
    assert updated.json()["permissions"] == ["examples:read", "examples:update"]

    assert (
        await client.delete(f"/api/v1/roles/{role['id']}", headers=admin_headers)
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/roles/{role['id']}", headers=admin_headers)
    ).status_code == 404


async def test_role_rejects_unknown_permissions_and_duplicate_names(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    unknown = await client.post(
        "/api/v1/roles", headers=admin_headers, json={"name": "Odd", "permissions": ["nope:nope"]}
    )
    duplicate = await client.post("/api/v1/roles", headers=admin_headers, json={"name": "MEMBER"})

    assert unknown.status_code == 422
    assert duplicate.status_code == 409


async def test_system_roles_are_protected(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    admin_role = await _role_id(client, admin_headers, ADMIN_ROLE)
    member_role = await _role_id(client, admin_headers, MEMBER_ROLE)

    assert (
        await client.delete(f"/api/v1/roles/{admin_role}", headers=admin_headers)
    ).status_code == 409
    rename = await client.put(
        f"/api/v1/roles/{member_role}", headers=admin_headers, json={"name": "renamed"}
    )
    reduce_admin = await client.put(
        f"/api/v1/roles/{admin_role}",
        headers=admin_headers,
        json={"name": ADMIN_ROLE, "permissions": []},
    )
    assert rename.status_code == reduce_admin.status_code == 409

    # Non-name edits to a non-admin system role are allowed.
    tweak = await client.put(
        f"/api/v1/roles/{member_role}",
        headers=admin_headers,
        json={"name": MEMBER_ROLE, "permissions": ["examples:read", "examples:create"]},
    )
    assert tweak.status_code == 200


async def test_role_in_use_cannot_be_deleted(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    member_role = await _role_id(client, admin_headers, MEMBER_ROLE)
    custom = await client.post("/api/v1/roles", headers=admin_headers, json={"name": "Temp"})
    await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "t@example.com",
            "full_name": "T",
            "password": PASSWORD,
            "role_ids": [custom.json()["id"], member_role],
        },
    )

    response = await client.delete(f"/api/v1/roles/{custom.json()['id']}", headers=admin_headers)

    assert response.status_code == 409
