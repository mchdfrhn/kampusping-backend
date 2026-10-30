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

EXAMPLES_READ = "examples:read"
EXAMPLES_CREATE = "examples:create"
EXAMPLES_UPDATE = "examples:update"
EXAMPLES_DELETE = "examples:delete"

PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef(USERS_READ, "View users"),
    PermissionDef(USERS_CREATE, "Create users"),
    PermissionDef(USERS_UPDATE, "Edit users"),
    PermissionDef(USERS_DELETE, "Delete users"),
    PermissionDef(ROLES_READ, "View roles and permissions"),
    PermissionDef(ROLES_CREATE, "Create roles"),
    PermissionDef(ROLES_UPDATE, "Edit roles"),
    PermissionDef(ROLES_DELETE, "Delete roles"),
    PermissionDef(EXAMPLES_READ, "View example items"),
    PermissionDef(EXAMPLES_CREATE, "Create example items"),
    PermissionDef(EXAMPLES_UPDATE, "Edit example items"),
    PermissionDef(EXAMPLES_DELETE, "Delete example items"),
)

ALL_PERMISSION_CODES: tuple[str, ...] = tuple(p.code for p in PERMISSIONS)

ADMIN_ROLE = "admin"
MEMBER_ROLE = "member"

# System roles: created by the seed. `admin` is re-synced to hold every permission on each seed;
# `member` is only created once so edits made in the UI are preserved.
SYSTEM_ROLES: dict[str, RoleDef] = {
    ADMIN_ROLE: RoleDef("Full access to every feature", ALL_PERMISSION_CODES),
    MEMBER_ROLE: RoleDef("Default read-only access", (EXAMPLES_READ,)),
}
