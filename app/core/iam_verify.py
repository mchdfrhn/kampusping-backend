from __future__ import annotations

from typing import Any

from app.core.config import settings


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract_verify_user_root(payload: dict[str, Any]) -> dict[str, Any]:
    user = payload.get("user")
    if isinstance(user, dict):
        return {**_as_dict(payload.get("data")), **user}
    return {**_as_dict(payload.get("data")), **payload}


def _collect_role_id(role_ids: list[int], raw: Any) -> None:
    try:
        role_id = int(raw)
    except (TypeError, ValueError):
        return
    if role_id > 0:
        role_ids.append(role_id)


def extract_rolesdivision_role_ids(payload: dict[str, Any]) -> list[int]:
    root = extract_verify_user_root(payload)
    role_ids: list[int] = []
    _collect_role_id(role_ids, root.get("role_id") or root.get("roleId"))

    for key in ("rolesdivision", "rolesDivision", "roles_division"):
        items = root.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                nested_role = item.get("role") if isinstance(item.get("role"), dict) else {}
                _collect_role_id(
                    role_ids,
                    item.get("role_id") or item.get("roleId") or nested_role.get("id") or item.get("id"),
                )
            else:
                _collect_role_id(role_ids, item)

    return sorted(set(role_ids))


def extract_verify_identity(payload: dict[str, Any]) -> dict[str, Any]:
    root = extract_verify_user_root(payload)
    return {
        "username": root.get("username") or root.get("email") or root.get("name") or "",
        "email": root.get("email"),
        "name": root.get("name") or root.get("full_name"),
    }


def is_group_admin(role_ids: list[int]) -> bool:
    return settings.group_admin_role_id in role_ids
