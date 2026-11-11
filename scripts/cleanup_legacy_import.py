"""Pembersihan sekali jalan untuk impor yang diterapkan SEBELUM migrasi 008.

Impor lama tidak punya log perubahan, jadi tidak bisa di-rollback lewat halaman Impor Data.
Skrip ini menghapus (soft delete) mahasiswa dan tagihan yang DIBUAT oleh impor itu. Baris
dikenali dari ``created_at == applied_at`` (apply memakai satu ``now`` untuk batch dan baris
barunya).

Yang TIDAK bisa dikembalikan: perubahan pada baris yang sudah ada sebelum impor (nominal, jatuh
tempo, status lunas), karena nilai lamanya tidak pernah disimpan.

Default-nya DRY-RUN: hanya menampilkan apa yang akan dihapus. Tidak ada perubahan tanpa --apply.

Cara pakai (jalankan dari folder kampusping-backend, dengan environment/.env yang benar):
    python scripts/cleanup_legacy_import.py --list
    python scripts/cleanup_legacy_import.py --batch-id 12 --batch-id 11
    python scripts/cleanup_legacy_import.py --batch-id 12 --batch-id 11 --report daftar.csv
    python scripts/cleanup_legacy_import.py --batch-id 12 --batch-id 11 --apply

Sebut semua impor yang ingin dibersihkan dalam SATU perintah: tagihan yang dibuat impor lain untuk
mahasiswa yang akan dihapus memblokir penghapusan (supaya tidak ada tagihan aktif tanpa mahasiswa).
"""

import argparse
import asyncio
import csv
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlalchemy.orm import noload  # noqa: E402

from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.modules.audit.service import record_audit  # noqa: E402
from app.modules.imports.constants import AUDIT_ENTITY_IMPORT, ImportStatus  # noqa: E402
from app.modules.imports.model import ImportBatch  # noqa: E402
from app.modules.invoices.model import Invoice  # noqa: E402
from app.modules.messages.constants import (  # noqa: E402
    SENT_STATUSES,
    CancelReason,
    MessageStatus,
)
from app.modules.messages.model import Message  # noqa: E402
from app.modules.students.model import Student  # noqa: E402
from app.shared.utils import utc_now  # noqa: E402

AUDIT_LEGACY_CLEANED = "import.legacy_cleaned"
CANCEL_ERROR = "Impor lama dibersihkan"
CHUNK = 500  # stay below the variable limit of older SQLite builds in IN (...) lookups
CONFIRM_WORD = "hapus"


class CleanupError(Exception):
    """A reason the cleanup cannot go ahead (shown to the operator as-is)."""


@dataclass
class Targets:
    batches: list[ImportBatch]
    students: list[Student]
    invoices: list[Invoice]
    created_by_batch: dict[
        int, tuple[list[int], list[int]]
    ]  # batch id -> (student ids, invoice ids)
    blocking_invoices: list[Invoice]  # active invoices of targeted students that are not targeted
    queued_messages: int
    sent_messages: int
    warnings: list[str] = field(default_factory=list)


def _chunks(values: Sequence[int]) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


async def _load_batch(db: AsyncSession, batch_id: int) -> ImportBatch:
    batch = await db.get(ImportBatch, batch_id)
    if batch is None or batch.deleted_at is not None:
        raise CleanupError(f"Impor {batch_id} tidak ditemukan.")
    if batch.status != ImportStatus.APPLIED.value:
        raise CleanupError(f"Impor {batch_id} berstatus '{batch.status}', bukan Diterapkan.")
    if batch.has_change_log:
        raise CleanupError(
            f"Impor {batch_id} punya log perubahan: batalkan lewat fitur rollback "
            "di halaman Impor Data, bukan lewat skrip ini."
        )
    if batch.applied_at is None:
        raise CleanupError(
            f"Impor {batch_id} tidak punya applied_at, baris barunya tidak dikenali."
        )
    return batch


async def _count_messages(
    db: AsyncSession, invoice_ids: Sequence[int], statuses: Sequence[str]
) -> int:
    total = 0
    for chunk in _chunks(invoice_ids):
        total += (
            await db.scalar(
                select(func.count(Message.id)).where(
                    Message.invoice_id.in_(chunk), Message.status.in_(statuses)
                )
            )
            or 0
        )
    return total


async def find_targets(db: AsyncSession, batch_ids: Sequence[int]) -> Targets:
    """What cleaning the given legacy batches would delete. Reads only."""
    batches = [await _load_batch(db, batch_id) for batch_id in dict.fromkeys(batch_ids)]

    students: dict[int, Student] = {}
    invoices: dict[int, Invoice] = {}
    created_by_batch: dict[int, tuple[list[int], list[int]]] = {}
    warnings: list[str] = []
    for batch in batches:
        found_students = list(
            await db.scalars(
                select(Student).where(
                    Student.created_at == batch.applied_at, Student.deleted_at.is_(None)
                )
            )
        )
        found_invoices = list(
            await db.scalars(
                select(Invoice)
                .where(Invoice.created_at == batch.applied_at, Invoice.deleted_at.is_(None))
                .options(noload(Invoice.student))
            )
        )
        students.update({s.id: s for s in found_students})
        invoices.update({i.id: i for i in found_invoices})
        created_by_batch[batch.id] = (
            [s.id for s in found_students],
            [i.id for i in found_invoices],
        )
        for label, found, key in (
            ("mahasiswa baru", len(found_students), "students_new"),
            ("tagihan baru", len(found_invoices), "invoices_new"),
        ):
            expected = batch.summary.get(key)
            if expected != found:
                warnings.append(
                    f"Impor {batch.id}: ringkasan mencatat {expected} {label}, "
                    f"ditemukan {found} yang masih aktif."
                )

    blocking: list[Invoice] = []
    for chunk in _chunks(sorted(students)):
        rows = await db.scalars(
            select(Invoice)
            .where(Invoice.student_id.in_(chunk), Invoice.deleted_at.is_(None))
            .options(noload(Invoice.student))
        )
        blocking.extend(i for i in rows if i.id not in invoices)

    invoice_ids = sorted(invoices)
    return Targets(
        batches=batches,
        students=sorted(students.values(), key=lambda s: s.nim),
        invoices=sorted(invoices.values(), key=lambda i: i.invoice_code),
        created_by_batch=created_by_batch,
        blocking_invoices=sorted(blocking, key=lambda i: i.invoice_code),
        queued_messages=await _count_messages(db, invoice_ids, [MessageStatus.QUEUED.value]),
        sent_messages=await _count_messages(db, invoice_ids, SENT_STATUSES),
        warnings=warnings,
    )


async def apply_cleanup(db: AsyncSession, targets: Targets, *, actor: str, now: datetime) -> None:
    """Soft-delete the targeted rows, cancel their queued messages, close the batches.

    Writes with UPDATE ... WHERE id IN (...), so it works on rows loaded by another session. The
    caller commits.
    """
    if targets.blocking_invoices:
        codes = ", ".join(i.invoice_code for i in targets.blocking_invoices[:10])
        raise CleanupError(
            f"Mahasiswa yang akan dihapus masih punya tagihan aktif dari impor lain ({codes}). "
            "Sertakan impor itu juga atau hapus tagihannya dulu."
        )

    audit_values = {"updated_by": actor, "updated_at": now}
    delete_values = {"deleted_at": now, "deleted_by": actor, **audit_values}
    for chunk in _chunks([s.id for s in targets.students]):
        await db.execute(update(Student).where(Student.id.in_(chunk)).values(**delete_values))
    invoice_ids = [i.id for i in targets.invoices]
    for chunk in _chunks(invoice_ids):
        await db.execute(update(Invoice).where(Invoice.id.in_(chunk)).values(**delete_values))
        await db.execute(
            update(Message)
            .where(Message.invoice_id.in_(chunk), Message.status == MessageStatus.QUEUED.value)
            .values(
                status=MessageStatus.CANCELLED.value,
                cancel_reason=CancelReason.MANUAL.value,
                error=CANCEL_ERROR,
                **audit_values,
            )
            .execution_options(synchronize_session=False)
        )

    for batch in targets.batches:
        student_ids, batch_invoice_ids = targets.created_by_batch[batch.id]
        await db.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch.id)
            .values(status=ImportStatus.ROLLED_BACK.value, rolled_back_at=now, **audit_values)
        )
        await record_audit(
            db,
            actor=actor,
            action=AUDIT_LEGACY_CLEANED,
            entity=AUDIT_ENTITY_IMPORT,
            entity_id=batch.id,
            detail={
                "legacy": True,
                "students_deleted": len(student_ids),
                "invoices_deleted": len(batch_invoice_ids),
            },
        )


def render_report(targets: Targets, *, limit: int) -> str:
    lines = ["=== Impor yang dibersihkan ==="]
    for batch in targets.batches:
        student_ids, invoice_ids = targets.created_by_batch[batch.id]
        lines.append(
            f"- Impor {batch.id}: {batch.file_name} (diterapkan {batch.applied_at}) -> "
            f"{len(student_ids)} mahasiswa baru, {len(invoice_ids)} tagihan baru"
        )
    lines += [
        "",
        f"Mahasiswa yang akan dihapus : {len(targets.students)}",
        f"Tagihan yang akan dihapus   : {len(targets.invoices)}",
        f"Pesan antre dibatalkan      : {targets.queued_messages}",
        f"Pesan sudah terkirim        : {targets.sent_messages} (tetap di log, tidak bisa ditarik)",
        "",
        "Tidak dikembalikan: perubahan pada baris yang sudah ada sebelum impor "
        "(nominal, jatuh tempo, status lunas).",
    ]
    for warning in targets.warnings:
        lines.append(f"[PERINGATAN] {warning}")
    if targets.blocking_invoices:
        codes = ", ".join(i.invoice_code for i in targets.blocking_invoices[:10])
        lines.append(
            f"[BLOKIR] {len(targets.blocking_invoices)} tagihan aktif dari impor lain milik "
            f"mahasiswa yang akan dihapus: {codes}"
        )
    names = {s.id: s for s in targets.students}
    lines += ["", f"Contoh mahasiswa (maks {limit}):"]
    lines += [f"  {s.nim}  {s.name}" for s in targets.students[:limit]]
    lines += ["", f"Contoh tagihan (maks {limit}):"]
    for invoice in targets.invoices[:limit]:
        owner = names.get(invoice.student_id)
        lines.append(
            f"  {invoice.invoice_code}  {owner.nim if owner else '-'}  "
            f"Rp{invoice.amount:,}  jatuh tempo {invoice.due_date}"
        )
    return "\n".join(lines)


def write_report(targets: Targets, path: Path) -> None:
    """The full list for review (names and NIM: personal data, delete the file afterwards)."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["jenis", "id", "nim_atau_kode", "nama", "nominal", "jatuh_tempo"])
        for student in targets.students:
            writer.writerow(["mahasiswa", student.id, student.nim, student.name, "", ""])
        by_id = {s.id: s for s in targets.students}
        for invoice in targets.invoices:
            owner = by_id.get(invoice.student_id)
            writer.writerow(
                [
                    "tagihan",
                    invoice.id,
                    invoice.invoice_code,
                    owner.name if owner else "",
                    invoice.amount,
                    invoice.due_date,
                ]
            )


async def print_legacy_batches(db: AsyncSession) -> None:
    batches = await db.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.status == ImportStatus.APPLIED.value,
            ImportBatch.has_change_log.is_(False),
            ImportBatch.deleted_at.is_(None),
        )
        .order_by(ImportBatch.applied_at)
    )
    print("Impor berstatus Diterapkan tanpa log perubahan:")
    for batch in batches:
        print(
            f"  id={batch.id}  {batch.file_name}  diterapkan {batch.applied_at}  "
            f"mahasiswa baru={batch.summary.get('students_new')}  "
            f"tagihan baru={batch.summary.get('invoices_new')}"
        )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bersihkan impor lama (tanpa log perubahan).")
    parser.add_argument("--list", action="store_true", help="tampilkan impor yang bisa dibersihkan")
    parser.add_argument("--batch-id", type=int, action="append", default=[], help="boleh diulang")
    parser.add_argument(
        "--apply", action="store_true", help="benar-benar menghapus (default dry-run)"
    )
    parser.add_argument("--yes", action="store_true", help="lewati pertanyaan konfirmasi")
    parser.add_argument("--report", type=Path, help="tulis daftar lengkap ke berkas CSV")
    parser.add_argument("--limit", type=int, default=20, help="jumlah contoh yang dicetak")
    parser.add_argument("--actor", default="legacy-cleanup", help="nama pelaku di audit")
    args = parser.parse_args(list(argv))
    if not args.list and not args.batch_id:
        parser.error("sebutkan --batch-id (atau --list untuk melihat daftar impor)")
    return args


async def run(args: argparse.Namespace) -> int:
    async with get_session_factory()() as db:
        if args.list:
            await print_legacy_batches(db)
            return 0
        try:
            targets = await find_targets(db, args.batch_id)
        except CleanupError as exc:
            print(f"[GAGAL] {exc}")
            return 1

        print(render_report(targets, limit=args.limit))
        if args.report:
            write_report(targets, args.report)
            print(
                f"\nDaftar lengkap ditulis ke {args.report} "
                "(berisi data pribadi, hapus setelah dicek)."
            )
        if targets.blocking_invoices:
            print("\n[GAGAL] Ada tagihan yang memblokir; tidak ada yang diubah.")
            return 1
        if not args.apply:
            print("\nDRY-RUN: tidak ada yang diubah. Tambahkan --apply untuk menghapus.")
            return 0

        if not args.yes:
            prompt = f"\nKetik '{CONFIRM_WORD}' untuk menghapus data di atas: "
            answer = await asyncio.to_thread(input, prompt)
            if answer.strip().lower() != CONFIRM_WORD:
                print("Dibatalkan, tidak ada yang diubah.")
                return 1
        try:
            await apply_cleanup(db, targets, actor=args.actor, now=utc_now())
            await db.commit()
        except CleanupError as exc:
            await db.rollback()
            print(f"[GAGAL] {exc}")
            return 1
        except Exception:
            await db.rollback()
            raise
        print(
            f"\n[OK] {len(targets.students)} mahasiswa dan {len(targets.invoices)} tagihan "
            f"dihapus (soft delete), {targets.queued_messages} pesan antre dibatalkan."
        )
        return 0


async def _main(argv: Sequence[str]) -> int:
    try:
        return await run(parse_args(argv))
    finally:
        await get_engine().dispose()


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
