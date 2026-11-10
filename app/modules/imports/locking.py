"""Serialises the operations that rewrite imported data (apply and rollback)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Arbitrary application-wide key for ``pg_advisory_xact_lock``.
IMPORT_LOCK_KEY = 7_204_001


async def lock_imports(db: AsyncSession) -> None:
    """Take the import lock until the transaction ends (PostgreSQL only).

    Without it a rollback could run while an apply is still uncommitted: the rollback would see
    no newer applied batch and delete rows the apply is about to attach new invoices to. SQLite
    has a single writer, so there is nothing to do there.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(select(func.pg_advisory_xact_lock(IMPORT_LOCK_KEY)))
