"""The per-row change log written when an import is applied (the basis of rollback)."""

from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.imports.model import ImportBatch, ImportChange


async def _make_batch() -> int:
    async with get_session_factory()() as db:
        batch = ImportBatch(source="siakad", file_name="x.csv", summary={"rows_total": 0})
        db.add(batch)
        await db.commit()
        return batch.id


async def test_import_change_roundtrip() -> None:
    batch_id = await _make_batch()
    async with get_session_factory()() as db:
        db.add(
            ImportChange(
                batch_id=batch_id,
                entity="invoice",
                entity_id=7,
                action="updated",
                before={"amount": 1},
                after={"amount": 2},
            )
        )
        await db.commit()

    async with get_session_factory()() as db:
        change = await db.scalar(select(ImportChange).where(ImportChange.batch_id == batch_id))
        assert change is not None
        assert change.entity == "invoice"
        assert change.entity_id == 7
        assert change.action == "updated"
        assert change.before == {"amount": 1}
        assert change.after == {"amount": 2}


async def test_batch_defaults() -> None:
    batch_id = await _make_batch()
    async with get_session_factory()() as db:
        batch = await db.get(ImportBatch, batch_id)
        assert batch is not None
        assert batch.has_change_log is False
        assert batch.rolled_back_at is None
