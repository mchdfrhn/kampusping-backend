import httpx
import pytest
from sqlalchemy import select

from app.core.permissions import ADMIN_ROLE, ALL_PERMISSION_CODES, PIMPINAN_ROLE
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
    ],
)
async def test_endpoints_require_authentication(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)

    assert response.status_code == 401


async def test_pimpinan_is_forbidden_from_admin_endpoints(
    client: httpx.AsyncClient, pimpinan_headers: Headers
) -> None:
    users = await client.get("/api/v1/users", headers=pimpinan_headers)
    roles = await client.get("/api/v1/roles", headers=pimpinan_headers)

    assert users.status_code == roles.status_code == 403
    assert users.json()["error"]["code"] == "forbidden"
    assert (
        users.json()["error"]["message"] == "Anda tidak memiliki izin untuk melakukan tindakan ini."
    )
    assert users.json()["error"]["details"] == {"required": ["users:read"]}
    # ...but being signed in is enough for the self-service endpoints.
    assert (await client.get("/api/v1/auth/me", headers=pimpinan_headers)).status_code == 200


async def test_deactivated_user_loses_access_immediately(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    pimpinan = await create_user("pimpinan@example.com", PIMPINAN_ROLE)
    pimpinan_headers = await auth_headers(client, "pimpinan@example.com")
    assert (await client.get("/api/v1/auth/me", headers=pimpinan_headers)).status_code == 200

    await client.put(
        f"/api/v1/users/{pimpinan.id}",
        headers=admin_headers,
        json={"full_name": "Pimpinan", "is_active": False, "role_ids": []},
    )

    assert (await client.get("/api/v1/auth/me", headers=pimpinan_headers)).status_code == 401
    assert (await login(client, "pimpinan@example.com")).status_code == 401


# --- Users --------------------------------------------------------------------------------


async def _role_id(client: httpx.AsyncClient, headers: Headers, name: str) -> int:
    page = (await client.get("/api/v1/roles", headers=headers)).json()
    return next(role["id"] for role in page["items"] if role["name"] == name)


async def test_admin_manages_users(client: httpx.AsyncClient, admin_headers: Headers) -> None:
    pimpinan_role = await _role_id(client, admin_headers, PIMPINAN_ROLE)

    created = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "New.User@Example.com",
            "full_name": "New User",
            "password": PASSWORD,
            "role_ids": [pimpinan_role],
        },
    )
    assert created.status_code == 201
    user = created.json()
    assert user["email"] == "new.user@example.com"
    assert [role["name"] for role in user["roles"]] == [PIMPINAN_ROLE]
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
    assert response.json()["error"]["message"] == "Pengguna dengan email ini sudah ada."


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
    assert bad_role.json()["error"]["message"] == "ID peran tidak dikenal: 9999"


async def test_delete_is_soft_and_revokes_sessions(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    pimpinan = await create_user("pimpinan@example.com", PIMPINAN_ROLE)
    await login(client, "pimpinan@example.com")

    response = await client.delete(f"/api/v1/users/{pimpinan.id}", headers=admin_headers)

    assert response.status_code == 204
    assert (
        await client.get(f"/api/v1/users/{pimpinan.id}", headers=admin_headers)
    ).status_code == 404
    async with get_session_factory()() as db:
        row = await db.scalar(select(User).where(User.id == pimpinan.id))
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
    assert delete.json()["error"]["message"] == "Anda tidak dapat menghapus akun Anda sendiri."
    assert (
        deactivate.json()["error"]["message"] == "Anda tidak dapat menonaktifkan akun Anda sendiri."
    )


async def test_list_contract_pagination_and_sorting(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    for index in range(5):
        await create_user(f"user{index}@example.com", PIMPINAN_ROLE)

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
    descriptions = {item["code"]: item["description"] for item in response.json()}
    assert descriptions["users:read"] == "Melihat pengguna"
    assert descriptions["roles:delete"] == "Menghapus peran"


async def test_role_crud_with_permissions(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    created = await client.post(
        "/api/v1/roles",
        headers=admin_headers,
        json={"name": "Reviewer", "description": "Read only", "permissions": ["roles:read"]},
    )
    assert created.status_code == 201
    role = created.json()
    assert role["permissions"] == ["roles:read"]
    assert role["is_system"] is False

    updated = await client.put(
        f"/api/v1/roles/{role['id']}",
        headers=admin_headers,
        json={"name": "Reviewer", "permissions": ["roles:read", "roles:update"]},
    )
    assert updated.json()["permissions"] == ["roles:read", "roles:update"]

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
    duplicate = await client.post("/api/v1/roles", headers=admin_headers, json={"name": "PIMPINAN"})

    assert unknown.status_code == 422
    assert unknown.json()["error"]["message"] == "Kode izin tidak dikenal: nope:nope"
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["message"] == "Peran dengan nama ini sudah ada."


async def test_system_roles_are_protected(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    admin_role = await _role_id(client, admin_headers, ADMIN_ROLE)
    pimpinan_role = await _role_id(client, admin_headers, PIMPINAN_ROLE)

    assert (
        await client.delete(f"/api/v1/roles/{admin_role}", headers=admin_headers)
    ).status_code == 409
    rename = await client.put(
        f"/api/v1/roles/{pimpinan_role}", headers=admin_headers, json={"name": "renamed"}
    )
    reduce_admin = await client.put(
        f"/api/v1/roles/{admin_role}",
        headers=admin_headers,
        json={"name": ADMIN_ROLE, "permissions": []},
    )
    assert rename.status_code == reduce_admin.status_code == 409
    assert rename.json()["error"]["message"] == "Peran sistem tidak dapat diganti namanya."
    assert reduce_admin.json()["error"]["message"] == "Peran admin selalu memiliki seluruh izin."

    # Non-name edits to a non-admin system role are allowed.
    tweak = await client.put(
        f"/api/v1/roles/{pimpinan_role}",
        headers=admin_headers,
        json={"name": PIMPINAN_ROLE, "permissions": ["roles:read", "roles:create"]},
    )
    assert tweak.status_code == 200


async def test_role_in_use_cannot_be_deleted(
    client: httpx.AsyncClient, admin_headers: Headers
) -> None:
    pimpinan_role = await _role_id(client, admin_headers, PIMPINAN_ROLE)
    custom = await client.post("/api/v1/roles", headers=admin_headers, json={"name": "Temp"})
    await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "t@example.com",
            "full_name": "T",
            "password": PASSWORD,
            "role_ids": [custom.json()["id"], pimpinan_role],
        },
    )

    response = await client.delete(f"/api/v1/roles/{custom.json()['id']}", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "Peran masih digunakan oleh 1 pengguna. Pindahkan penggunanya terlebih dahulu."
    )
