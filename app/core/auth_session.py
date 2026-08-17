from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.iam_verify import extract_rolesdivision_role_ids, is_group_admin
from app.modules.app_roles.repository import list_active_app_allowed_roles


def _role_payload(row) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "iam_role_id": int(row.iam_role_id),
        "display_name": str(row.display_name),
        "auth_slug": row.auth_slug,
        "role_type": str(row.role_type or "").strip().lower(),
        "sort_order": int(row.sort_order),
        "is_active": bool(row.is_active),
    }


def resolve_auth_session(db: Session, verify_payload: dict[str, Any]) -> dict[str, Any]:
    division_role_ids = extract_rolesdivision_role_ids(verify_payload)
    group_admin = is_group_admin(division_role_ids)
    allowed_rows = list_active_app_allowed_roles(db)
    matched_rows = [row for row in allowed_rows if int(row.iam_role_id) in set(division_role_ids)]
    matched_roles = [_role_payload(row) for row in matched_rows]
    primary = matched_roles[0] if matched_roles else None

    return {
        "has_access": bool(matched_rows) or group_admin,
        "is_group_admin": group_admin,
        "division_role_ids": division_role_ids,
        "display_role": primary["display_name"] if primary else None,
        "app_role": primary,
        "matched_roles": matched_roles,
    }
