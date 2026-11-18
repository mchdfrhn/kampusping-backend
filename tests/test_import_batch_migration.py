"""Migration 011 adds ``kind`` and ``details`` to ``import_batches``."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "011_import_batch_kind_and_details.py"
)


def load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_011", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine() -> Iterator[sa.Engine]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE import_batches (id INTEGER PRIMARY KEY, file_name VARCHAR(255))")
        )
        connection.execute(sa.text("INSERT INTO import_batches (file_name) VALUES ('old.csv')"))
    yield engine
    engine.dispose()


def columns(engine: sa.Engine) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns("import_batches")}


def test_upgrade_adds_columns_and_old_rows_become_standard(engine: sa.Engine) -> None:
    migration = load_migration()
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()

    assert {"kind", "details"} <= columns(engine)
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT kind, details FROM import_batches")).one()
    assert tuple(row) == ("standard", None)


def test_downgrade_removes_the_columns(engine: sa.Engine) -> None:
    migration = load_migration()
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()

    assert not {"kind", "details"} & columns(engine)
