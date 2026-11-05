"""Migration 007 grants the report permissions to system roles that already exist."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.base import Base
from app.modules.roles.model import Permission, Role, role_permissions

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "007_reports_permissions.py"
)
REPORT_CODES = {"reports:read", "reports:export"}


def load_migration() -> ModuleType:
    # The file name starts with a digit, so it cannot be imported with a plain import statement.
    spec = importlib.util.spec_from_file_location("migration_007", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine("sqlite://")
    tables = [Base.metadata.tables[name] for name in ("permissions", "roles", "role_permissions")]
    Base.metadata.create_all(engine, tables=tables)
    yield engine
    engine.dispose()


def add_role(session: Session, name: str, codes: tuple[str, ...] = ()) -> None:
    permissions = []
    for code in codes:
        permission = session.scalar(sa.select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code, description=code)
            session.add(permission)
        permissions.append(permission)
    session.add(
        Role(
            name=name,
            description="",
            is_system=True,
            permissions=permissions,
            created_by="test",
            updated_by="test",
        )
    )
    session.commit()


def codes_by_role(engine: sa.Engine) -> dict[str, set[str]]:
    with Session(engine) as session:
        return {
            role.name: {permission.code for permission in role.permissions}
            for role in session.scalars(sa.select(Role)).all()
        }


def report_permission_count(engine: sa.Engine) -> int:
    with engine.connect() as connection:
        return (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(Permission.__table__)
                .where(Permission.code.in_(REPORT_CODES))
            )
            or 0
        )


def link_count(engine: sa.Engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(sa.select(sa.func.count()).select_from(role_permissions)) or 0


def test_grant_links_existing_system_roles(engine: sa.Engine) -> None:
    with Session(engine) as session:
        add_role(session, "staf_keuangan", ("students:read",))
        add_role(session, "pimpinan", ("dashboard:read",))
        add_role(session, "lainnya", ("students:read",))

    with engine.begin() as connection:
        load_migration().grant_reports_permissions(connection)

    granted = codes_by_role(engine)
    assert granted["staf_keuangan"] >= REPORT_CODES
    assert granted["pimpinan"] >= REPORT_CODES
    assert granted["lainnya"] == {"students:read"}
    assert report_permission_count(engine) == 2


def test_grant_keeps_the_other_permissions_of_a_role(engine: sa.Engine) -> None:
    with Session(engine) as session:
        add_role(session, "pimpinan", ("dashboard:read",))

    with engine.begin() as connection:
        load_migration().grant_reports_permissions(connection)

    assert codes_by_role(engine)["pimpinan"] == {"dashboard:read", *REPORT_CODES}


def test_grant_is_idempotent(engine: sa.Engine) -> None:
    with Session(engine) as session:
        add_role(session, "staf_keuangan")
    migration = load_migration()

    with engine.begin() as connection:
        migration.grant_reports_permissions(connection)
    links_after_first_run = link_count(engine)
    with engine.begin() as connection:
        migration.grant_reports_permissions(connection)

    assert report_permission_count(engine) == 2
    assert link_count(engine) == links_after_first_run == 2


def test_grant_without_roles_only_creates_permissions(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        load_migration().grant_reports_permissions(connection)

    assert report_permission_count(engine) == 2
    assert link_count(engine) == 0


def test_revoke_removes_links_and_permissions(engine: sa.Engine) -> None:
    with Session(engine) as session:
        add_role(session, "pimpinan", ("dashboard:read",))
    migration = load_migration()
    with engine.begin() as connection:
        migration.grant_reports_permissions(connection)

    with engine.begin() as connection:
        migration.revoke_reports_permissions(connection)

    assert report_permission_count(engine) == 0
    assert codes_by_role(engine)["pimpinan"] == {"dashboard:read"}
