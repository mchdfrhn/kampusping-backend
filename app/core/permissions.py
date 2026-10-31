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

PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef(USERS_READ, "Melihat pengguna"),
    PermissionDef(USERS_CREATE, "Menambah pengguna"),
    PermissionDef(USERS_UPDATE, "Mengubah pengguna"),
    PermissionDef(USERS_DELETE, "Menghapus pengguna"),
    PermissionDef(ROLES_READ, "Melihat peran dan izin"),
    PermissionDef(ROLES_CREATE, "Menambah peran"),
    PermissionDef(ROLES_UPDATE, "Mengubah peran"),
    PermissionDef(ROLES_DELETE, "Menghapus peran"),
)

ALL_PERMISSION_CODES: tuple[str, ...] = tuple(p.code for p in PERMISSIONS)

ADMIN_ROLE = "admin"
MEMBER_ROLE = "member"

# System roles: created by the seed. `admin` is re-synced to hold every permission on each seed;
# `member` is only created once so edits made in the UI are preserved.
SYSTEM_ROLES: dict[str, RoleDef] = {
    ADMIN_ROLE: RoleDef("Akses penuh ke seluruh fitur", ALL_PERMISSION_CODES),
    MEMBER_ROLE: RoleDef("Akses default tanpa izin", ()),
}
