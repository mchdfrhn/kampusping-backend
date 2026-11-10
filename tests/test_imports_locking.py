"""The import lock serialises apply and rollback (PostgreSQL only; SQLite has one writer)."""

from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.modules.imports.locking import lock_imports


class _Dialect:
    name = "postgresql"


class _Bind:
    dialect = _Dialect()


class _FakePostgresSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def get_bind(self) -> _Bind:
        return _Bind()

    async def execute(self, statement: object) -> None:
        self.statements.append(str(statement.compile(dialect=postgresql.dialect())))  # type: ignore[attr-defined]


async def test_lock_imports_takes_a_postgres_advisory_transaction_lock() -> None:
    session = _FakePostgresSession()

    await lock_imports(cast(AsyncSession, session))

    assert len(session.statements) == 1
    assert "pg_advisory_xact_lock" in session.statements[0]


async def test_lock_imports_does_nothing_on_sqlite() -> None:
    async with get_session_factory()() as db:
        await lock_imports(db)  # must not raise
