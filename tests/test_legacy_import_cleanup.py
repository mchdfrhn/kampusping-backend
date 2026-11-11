"""scripts/cleanup_legacy_import.py: one-off cleanup for imports applied before migration 008."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.modules.audit.model import AuditEvent
from app.modules.imports.model import ImportBatch
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.students.model import Student

from .imports_helpers import import_lines, invoice_by_code, line, preview

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_legacy_import.py"
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)


def load_script() -> ModuleType:
    # scripts/ is not a package, so load the file by path (like the migration tests do).
    spec = importlib.util.spec_from_file_location("cleanup_legacy_import", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load_script()


async def make_legacy(*batch_ids: int) -> None:
    """Pretend the batches were applied before the change log existed."""
    async with get_session_factory()() as db:
        for batch_id in batch_ids:
            batch = await db.get(ImportBatch, batch_id)
            assert batch is not None
            batch.has_change_log = False
        await db.commit()


async def two_legacy_batches(client: httpx.AsyncClient, headers: dict[str, str]) -> tuple[int, int]:
    """A creates 2201/INV-1; B changes INV-1 and creates 2202/INV-2."""
    first = await import_lines(client, headers, line(nim="2201", code="INV-1", amount="5000000"))
    second = await import_lines(
        client,
        headers,
        line(nim="2201", code="INV-1", amount="6000000"),
        line(nim="2202", name="Siti", code="INV-2"),
    )
    await make_legacy(first["id"], second["id"])
    return first["id"], second["id"]


async def find(*batch_ids: int) -> object:
    async with get_session_factory()() as db:
        return await script.find_targets(db, list(batch_ids))


async def test_finds_only_rows_created_by_the_batch(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    _, second = await two_legacy_batches(client, staf_headers)

    targets = await find(second)

    assert [s.nim for s in targets.students] == ["2202"]  # type: ignore[attr-defined]
    assert [i.invoice_code for i in targets.invoices] == ["INV-2"]  # type: ignore[attr-defined]
    assert targets.blocking_invoices == []  # type: ignore[attr-defined]
    assert targets.warnings == []  # type: ignore[attr-defined]


async def test_refuses_batches_that_are_not_legacy_applied_batches(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    current = await import_lines(client, staf_headers, line(code="INV-1"))  # has a change log
    pending = await preview(client, staf_headers, line(nim="2202", name="Siti", code="INV-2"))

    with pytest.raises(script.CleanupError, match="rollback"):
        await find(current["id"])
    with pytest.raises(script.CleanupError, match="bukan Diterapkan"):
        await find(pending["id"])
    with pytest.raises(script.CleanupError, match="tidak ditemukan"):
        await find(99999)


async def test_blocks_when_a_targeted_student_has_other_active_invoices(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first = await import_lines(client, staf_headers, line(nim="2201", code="INV-1"))
    second = await import_lines(client, staf_headers, line(nim="2201", code="INV-3"))
    await make_legacy(first["id"], second["id"])

    targets = await find(first["id"])  # student 2201 is created by A, but INV-3 comes from B

    assert [i.invoice_code for i in targets.blocking_invoices] == ["INV-3"]  # type: ignore[attr-defined]
    async with get_session_factory()() as db:
        with pytest.raises(script.CleanupError, match="INV-3"):
            await script.apply_cleanup(db, targets, actor="tester", now=NOW)  # type: ignore[arg-type]


async def test_apply_soft_deletes_cancels_queued_and_marks_batch(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    first, second = await two_legacy_batches(client, staf_headers)
    invoice = await invoice_by_code("INV-2")
    ids: dict[str, int] = {}
    async with get_session_factory()() as db:
        for status in ("queued", "sent"):
            message = Message(
                student_id=invoice.student_id,
                invoice_id=invoice.id,
                kind="manual",
                scheduled_date=NOW.date(),
                body="Halo",
                status=status,
            )
            db.add(message)
            await db.flush()
            ids[status] = message.id
        await db.commit()
    targets = await find(second)

    async with get_session_factory()() as db:
        await script.apply_cleanup(db, targets, actor="tester", now=NOW)  # type: ignore[arg-type]
        await db.commit()

    async with get_session_factory()() as db:
        siti = await db.scalar(select(Student).where(Student.nim == "2202"))
        budi = await db.scalar(select(Student).where(Student.nim == "2201"))
        inv2 = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-2"))
        inv1 = await db.scalar(select(Invoice).where(Invoice.invoice_code == "INV-1"))
        assert siti is not None
        assert inv2 is not None
        assert budi is not None
        assert inv1 is not None
        assert (siti.deleted_by, inv2.deleted_by) == ("tester", "tester")
        assert siti.deleted_at is not None
        assert inv2.deleted_at is not None
        assert budi.deleted_at is None  # not created by B: untouched
        assert inv1.deleted_at is None
        queued = await db.get(Message, ids["queued"])
        sent = await db.get(Message, ids["sent"])
        assert queued is not None
        assert sent is not None
        assert (queued.status, queued.cancel_reason) == ("cancelled", "manual")
        assert sent.status == "sent"
        batch = await db.get(ImportBatch, second)
        assert batch is not None
        assert batch.status == "rolled_back"
        assert batch.rolled_back_at is not None
        other = await db.get(ImportBatch, first)
        assert other is not None
        assert other.status == "applied"
        events = (
            await db.scalars(select(AuditEvent).where(AuditEvent.action == "import.legacy_cleaned"))
        ).all()
        assert len(events) == 1
        assert events[0].detail is not None
        assert events[0].detail["students_deleted"] == 1


async def test_warns_when_found_rows_differ_from_the_batch_summary(
    client: httpx.AsyncClient, staf_headers: dict[str, str]
) -> None:
    _, second = await two_legacy_batches(client, staf_headers)
    async with get_session_factory()() as db:
        student = await db.scalar(select(Student).where(Student.nim == "2202"))
        assert student is not None
        student.deleted_at = NOW  # already gone, so fewer active rows than the summary says
        await db.commit()

    targets = await find(second)

    assert any("mahasiswa" in w for w in targets.warnings)  # type: ignore[attr-defined]


async def test_dry_run_changes_nothing_and_apply_needs_confirmation(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, second = await two_legacy_batches(client, staf_headers)

    code = await script.run(script.parse_args(["--batch-id", str(second)]))

    out = capsys.readouterr().out
    assert code == 0
    assert "DRY-RUN" in out
    assert "2202" in out
    assert "INV-2" in out
    assert (await invoice_by_code("INV-2")).deleted_at is None

    monkeypatch.setattr("builtins.input", lambda _prompt="": "tidak")
    refused = await script.run(script.parse_args(["--batch-id", str(second), "--apply"]))
    assert refused == 1
    assert (await invoice_by_code("INV-2")).deleted_at is None

    done = await script.run(script.parse_args(["--batch-id", str(second), "--apply", "--yes"]))
    assert done == 0
    assert (await invoice_by_code("INV-2")).deleted_at is not None


async def test_list_shows_applied_batches_without_a_change_log(
    client: httpx.AsyncClient,
    staf_headers: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    first, _ = await two_legacy_batches(client, staf_headers)
    await import_lines(client, staf_headers, line(nim="2209", name="Baru", code="INV-9"))

    code = await script.run(script.parse_args(["--list"]))

    out = capsys.readouterr().out
    assert code == 0
    assert str(first) in out
    assert "INV-9" not in out
