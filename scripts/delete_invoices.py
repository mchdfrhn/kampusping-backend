"""Script untuk menghapus data tagihan (invoices) dari database KampusPing secara aman.

Cara penggunaan:
    python scripts/delete_invoices.py --dry-run --all
    python scripts/delete_invoices.py --all
    python scripts/delete_invoices.py --semester "2024/2025 Ganjil"
    python scripts/delete_invoices.py --file tagihan_salah.xlsx
    python scripts/delete_invoices.py --all --include-imports
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Pastikan folder kampusping-backend ada di sys.path sebelum mengimpor app
BASE_DIR = Path(__file__).resolve().parent.parent
if not (BASE_DIR / "app").exists():
    BASE_DIR = Path.cwd()
sys.path.insert(0, str(BASE_DIR))
os.chdir(str(BASE_DIR))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.modules.imports.model import ImportBatch, ImportRowError  # noqa: E402
from app.modules.invoices.file_parser import extract_invoice_codes_from_file  # noqa: E402
from app.modules.invoices.model import Invoice  # noqa: E402
from app.modules.messages.model import Message  # noqa: E402


async def _inspect_counts(
    semester: str | None, codes: list[str] | None, include_imports: bool
) -> tuple[list[int], int, int]:
    session_factory = get_session_factory()
    async with session_factory() as db:
        invoice_stmt = select(Invoice.id)
        if codes:
            invoice_stmt = invoice_stmt.where(Invoice.invoice_code.in_(codes))
        elif semester:
            invoice_stmt = invoice_stmt.where(Invoice.semester == semester)
        invoice_ids = list((await db.scalars(invoice_stmt)).all())

        if not invoice_ids:
            return [], 0, 0

        msg_count_stmt = select(func.count(Message.id)).where(Message.invoice_id.in_(invoice_ids))
        messages_count = (await db.scalar(msg_count_stmt)) or 0

        batches_count = 0
        if include_imports:
            batch_stmt = select(func.count(ImportBatch.id))
            if semester:
                batch_stmt = batch_stmt.where(ImportBatch.semester == semester)
            batches_count = (await db.scalar(batch_stmt)) or 0

        return invoice_ids, messages_count, batches_count


async def _perform_deletion(
    invoice_ids: list[int],
    messages_count: int,
    semester: str | None,
    include_imports: bool,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        if messages_count > 0:
            await db.execute(delete(Message).where(Message.invoice_id.in_(invoice_ids)))
            print(f"[OK] Berhasil menghapus {messages_count:,} pesan terkait di antrean.")

        await db.execute(delete(Invoice).where(Invoice.id.in_(invoice_ids)))
        print(f"[OK] Berhasil menghapus {len(invoice_ids):,} tagihan.")

        if include_imports:
            batch_query = select(ImportBatch.id)
            if semester:
                batch_query = batch_query.where(ImportBatch.semester == semester)
            batch_ids = list((await db.scalars(batch_query)).all())
            if batch_ids:
                await db.execute(
                    delete(ImportRowError).where(ImportRowError.batch_id.in_(batch_ids))
                )
                await db.execute(delete(ImportBatch).where(ImportBatch.id.in_(batch_ids)))
                print(f"[OK] Berhasil menghapus {len(batch_ids):,} batch impor dan log-nya.")

        await db.commit()
        print("\n[SELESAI] Data berhasil dihapus dari database.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hapus data tagihan (invoices) dari database KampusPing."
    )
    parser.add_argument(
        "--all", action="store_true", help="Hapus seluruh tagihan di semua semester"
    )
    parser.add_argument(
        "--semester",
        type=str,
        default=None,
        help="Filter semester tertentu (misal: '2024/2025 Ganjil')",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Hapus tagihan berdasarkan file Excel/CSV",
    )
    parser.add_argument(
        "--include-imports",
        action="store_true",
        help="Turut menghapus riwayat batch impor dan error log-nya",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Cek jumlah data tanpa benar-benar menghapus"
    )
    parser.add_argument(
        "-y", "--yes", "--force", action="store_true", dest="force", help="Lewati konfirmasi 'ya'"
    )

    args = parser.parse_args()

    if not args.all and not args.semester and not args.file:
        parser.error(
            "Tentukan target penghapusan: gunakan flag '--all', "
            "'--semester \"<nama>\"', atau '--file \"<path berkas>\"'."
        )

    codes: list[str] | None = None
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            parser.error(f"Berkas tidak ditemukan: {args.file}")
        with open(file_path, "rb") as f:
            content = f.read()
        codes = extract_invoice_codes_from_file(file_path.name, content)
        print(f"[INFO] Berhasil mengekstrak {len(codes):,} kode tagihan dari berkas.")

    invoice_ids, messages_count, batches_count = asyncio.run(
        _inspect_counts(args.semester, codes, args.include_imports)
    )

    if not invoice_ids:
        print("[INFO] Tidak ditemukan data tagihan yang sesuai dengan kriteria.")
        return

    print("=== RINGKASAN DATA YANG AKAN DIHAPUS ===")
    if args.file:
        print(f"Target Berkas         : {args.file} ({len(codes):,} kode unik)")
    else:
        print(f"Target Semester       : {args.semester if args.semester else 'SEMUA SEMESTER'}")
    print(f"Total Tagihan (Invoice): {len(invoice_ids):,} data")
    print(f"Pesan Antrean/Terkirim: {messages_count:,} data")
    if args.include_imports:
        print(f"Batch Riwayat Impor   : {batches_count:,} data")
    print("========================================")

    if args.dry_run:
        print("[DRY-RUN] Mode simulasi aktif. Tidak ada perubahan yang disimpan ke database.")
        return

    if not args.force:
        prompt_text = "Apakah Anda yakin ingin menghapus data di atas? (ketik 'ya' untuk lanjut): "
        confirm = input(prompt_text)
        if confirm.strip().lower() != "ya":
            print("[BATAL] Operasi dibatalkan oleh pengguna.")
            return

    asyncio.run(
        _perform_deletion(
            invoice_ids=invoice_ids,
            messages_count=messages_count,
            semester=args.semester,
            include_imports=args.include_imports,
        )
    )


if __name__ == "__main__":
    main()
