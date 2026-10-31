from enum import StrEnum


class ImportStatus(StrEnum):
    PREVIEW = "preview"
    APPLIED = "applied"
    DISCARDED = "discarded"


AUDIT_IMPORT_PREVIEWED = "import.previewed"
AUDIT_IMPORT_APPLIED = "import.applied"
AUDIT_IMPORT_DISCARDED = "import.discarded"
AUDIT_ENTITY_IMPORT = "import_batch"

SUMMARY_KEYS = (
    "rows_total",
    "rows_valid",
    "rows_error",
    "students_new",
    "students_updated",
    "invoices_new",
    "invoices_updated",
    "invoices_skipped",
)

# Only this many row issues are stored per batch; ``summary.rows_error`` still counts all of them.
MAX_STORED_ISSUES = 500
FILE_NAME_MAX = 255
# Variable-count limit of older SQLite builds is 999; stay well below it in IN (...) lookups.
LOOKUP_CHUNK = 500

MSG_BATCH_NOT_FOUND = "Impor tidak ditemukan."
MSG_BATCH_NOT_PREVIEW = (
    "Impor ini sudah diproses atau dibatalkan, "
    "sehingga tidak dapat diterapkan atau dibatalkan lagi."
)
MSG_DUPLICATE_CODE = "Kode tagihan duplikat dalam berkas (baris {first_row})."
MSG_NAME_REQUIRED = "Nama wajib diisi untuk mahasiswa baru."
MSG_CODE_OTHER_STUDENT = "Kode tagihan sudah terdaftar untuk NIM lain."
MSG_NO_VALID_ROWS = "Tidak ada baris valid untuk diterapkan."
