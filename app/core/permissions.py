"""Permission and default-role catalog.

Permissions are code-defined (``resource:action``) and synced into the ``permissions`` table by
``python -m app.cli seed``. Roles are data: admins compose them from these permissions at runtime.
Routers reference the constants below, never numeric ids.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str
    description: str


@dataclass(frozen=True, slots=True)
class RoleDef:
    description: str
    permissions: tuple[str, ...]


USERS_READ = "users:read"
USERS_CREATE = "users:create"
USERS_UPDATE = "users:update"
USERS_DELETE = "users:delete"

ROLES_READ = "roles:read"
ROLES_CREATE = "roles:create"
ROLES_UPDATE = "roles:update"
ROLES_DELETE = "roles:delete"

DASHBOARD_READ = "dashboard:read"

IMPORTS_READ = "imports:read"
IMPORTS_CREATE = "imports:create"

STUDENTS_READ = "students:read"
STUDENTS_UPDATE = "students:update"

INVOICES_READ = "invoices:read"
INVOICES_UPDATE = "invoices:update"

TEMPLATES_READ = "templates:read"
TEMPLATES_CREATE = "templates:create"
TEMPLATES_UPDATE = "templates:update"
TEMPLATES_DELETE = "templates:delete"

SCHEDULES_READ = "schedules:read"
SCHEDULES_CREATE = "schedules:create"
SCHEDULES_UPDATE = "schedules:update"
SCHEDULES_DELETE = "schedules:delete"

MESSAGES_READ = "messages:read"
MESSAGES_SEND = "messages:send"
MESSAGES_CANCEL = "messages:cancel"

SETTINGS_READ = "settings:read"
SETTINGS_UPDATE = "settings:update"

AUDIT_READ = "audit:read"

PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef(USERS_READ, "Melihat pengguna"),
    PermissionDef(USERS_CREATE, "Menambah pengguna"),
    PermissionDef(USERS_UPDATE, "Mengubah pengguna"),
    PermissionDef(USERS_DELETE, "Menghapus pengguna"),
    PermissionDef(ROLES_READ, "Melihat peran dan izin"),
    PermissionDef(ROLES_CREATE, "Menambah peran"),
    PermissionDef(ROLES_UPDATE, "Mengubah peran"),
    PermissionDef(ROLES_DELETE, "Menghapus peran"),
    PermissionDef(DASHBOARD_READ, "Melihat dasbor"),
    PermissionDef(IMPORTS_READ, "Melihat riwayat impor"),
    PermissionDef(IMPORTS_CREATE, "Mengunggah dan memproses impor tagihan"),
    PermissionDef(STUDENTS_READ, "Melihat data mahasiswa"),
    PermissionDef(STUDENTS_UPDATE, "Mengubah data mahasiswa"),
    PermissionDef(INVOICES_READ, "Melihat tagihan"),
    PermissionDef(INVOICES_UPDATE, "Mengubah tagihan"),
    PermissionDef(TEMPLATES_READ, "Melihat templat pesan"),
    PermissionDef(TEMPLATES_CREATE, "Menambah templat pesan"),
    PermissionDef(TEMPLATES_UPDATE, "Mengubah templat pesan"),
    PermissionDef(TEMPLATES_DELETE, "Menghapus templat pesan"),
    PermissionDef(SCHEDULES_READ, "Melihat jadwal pengingat"),
    PermissionDef(SCHEDULES_CREATE, "Menambah jadwal pengingat"),
    PermissionDef(SCHEDULES_UPDATE, "Mengubah jadwal pengingat"),
    PermissionDef(SCHEDULES_DELETE, "Menghapus jadwal pengingat"),
    PermissionDef(MESSAGES_READ, "Melihat riwayat pesan"),
    PermissionDef(MESSAGES_SEND, "Mengirim pesan manual"),
    PermissionDef(MESSAGES_CANCEL, "Membatalkan pesan terjadwal"),
    PermissionDef(SETTINGS_READ, "Melihat pengaturan"),
    PermissionDef(SETTINGS_UPDATE, "Mengubah pengaturan"),
    PermissionDef(AUDIT_READ, "Melihat jejak audit"),
)

ALL_PERMISSION_CODES: tuple[str, ...] = tuple(p.code for p in PERMISSIONS)

ADMIN_ROLE = "admin"
STAF_KEUANGAN_ROLE = "staf_keuangan"
PIMPINAN_ROLE = "pimpinan"

# Staf keuangan runs the daily reminder workflow: everything except user/role administration
# and the audit trail.
STAF_KEUANGAN_PERMISSION_CODES: tuple[str, ...] = tuple(
    code for code in ALL_PERMISSION_CODES if not code.startswith(("users:", "roles:", "audit:"))
)

# System roles: created by the seed. `admin` is re-synced to hold every permission on each seed;
# the other roles are only created once so edits made in the UI are preserved.
SYSTEM_ROLES: dict[str, RoleDef] = {
    ADMIN_ROLE: RoleDef("Akses penuh ke seluruh fitur", ALL_PERMISSION_CODES),
    STAF_KEUANGAN_ROLE: RoleDef(
        "Mengelola impor tagihan, templat, jadwal, dan riwayat pesan",
        STAF_KEUANGAN_PERMISSION_CODES,
    ),
    PIMPINAN_ROLE: RoleDef("Hanya melihat dasbor ringkasan", (DASHBOARD_READ,)),
}
