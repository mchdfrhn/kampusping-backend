from sqlalchemy import select

from app.core.permissions import (
    ADMIN_ROLE,
    ALL_PERMISSION_CODES,
    DASHBOARD_READ,
    PIMPINAN_ROLE,
    STAF_KEUANGAN_ROLE,
)
from app.db.seed import seed_reference_data
from app.db.session import get_session_factory
from app.modules.roles.model import Role


async def _role_codes(name: str) -> set[str]:
    async with get_session_factory()() as db:
        role = await db.scalar(select(Role).where(Role.name == name))
        assert role is not None
        return {permission.code for permission in role.permissions}


async def test_admin_holds_every_permission() -> None:
    assert await _role_codes(ADMIN_ROLE) == set(ALL_PERMISSION_CODES)


async def test_staf_keuangan_has_everything_except_users_roles_audit() -> None:
    codes = await _role_codes(STAF_KEUANGAN_ROLE)

    assert codes
    assert not [code for code in codes if code.startswith(("users:", "roles:", "audit:"))]
    expected = {
        code for code in ALL_PERMISSION_CODES if not code.startswith(("users:", "roles:", "audit:"))
    }
    assert codes == expected


async def test_pimpinan_has_only_dashboard_read() -> None:
    assert await _role_codes(PIMPINAN_ROLE) == {DASHBOARD_READ}


async def test_seed_is_idempotent_and_keeps_edited_non_admin_roles() -> None:
    async with get_session_factory()() as db:
        role = await db.scalar(select(Role).where(Role.name == STAF_KEUANGAN_ROLE))
        assert role is not None
        removed = role.permissions.pop()
        removed_code = removed.code
        await db.commit()

    async with get_session_factory()() as db:
        await seed_reference_data(db)
        await seed_reference_data(db)

    assert removed_code not in await _role_codes(STAF_KEUANGAN_ROLE)
    assert await _role_codes(ADMIN_ROLE) == set(ALL_PERMISSION_CODES)
    async with get_session_factory()() as db:
        names = list(await db.scalars(select(Role.name)))
    assert sorted(names) == sorted([ADMIN_ROLE, STAF_KEUANGAN_ROLE, PIMPINAN_ROLE])
