"""Role type strings live here. Numeric IAM IDs live in app_allowed_roles (DB)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.reference import AppAllowedRole
from app.modules.app_roles.repository import list_active_app_allowed_roles

ROLE_TYPE_SUPER_ADMIN = "super_admin"
ROLE_TYPE_ADMIN = "admin"
ROLE_TYPE_DEALER = "dealer"
ROLE_TYPE_EMPLOYEE = "employee"

ADMIN_ROLE_TYPES = frozenset({ROLE_TYPE_SUPER_ADMIN, ROLE_TYPE_ADMIN})


@dataclass(frozen=True)
class RoleCatalog:
    rows: tuple[AppAllowedRole, ...]

    @classmethod
    def from_db(cls, db: Session) -> RoleCatalog:
        return cls(tuple(list_active_app_allowed_roles(db)))

    def iam_ids_for_type(self, role_type: str) -> list[int]:
        return [int(row.iam_role_id) for row in self.rows if row.role_type == role_type]

    def slug_for_iam_id(self, iam_role_id: int) -> str | None:
        for row in self.rows:
            if int(row.iam_role_id) == int(iam_role_id):
                return row.auth_slug
        return None

    def role_type_for_iam_id(self, iam_role_id: int) -> str | None:
        for row in self.rows:
            if int(row.iam_role_id) == int(iam_role_id):
                return row.role_type
        return None


def get_role_catalog(db: Session) -> RoleCatalog:
    return RoleCatalog.from_db(db)


def user_role_types(user: Any) -> set[str]:
    types: set[str] = set()
    for matched in getattr(user, "matched_roles", None) or []:
        role_type = matched.get("role_type") if isinstance(matched, dict) else None
        if isinstance(role_type, str) and role_type.strip():
            types.add(role_type.strip().lower())
    return types


def user_has_role_type(user: Any, role_type: str) -> bool:
    return role_type in user_role_types(user)


def user_has_any_role_type(user: Any, *role_types: str) -> bool:
    current = user_role_types(user)
    return any(role_type in current for role_type in role_types)
