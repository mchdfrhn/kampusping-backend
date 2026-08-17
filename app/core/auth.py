from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.api.deps import get_primary_db
from app.core.auth_session import resolve_auth_session
from app.core.config import settings
from app.core.constants import DEBUG_ROLE_IDS_HEADER, DEBUG_USERNAME_HEADER, FORBIDDEN, UNAUTHORIZED
from app.core.exceptions import AppException
from app.core.iam_verify import extract_verify_identity
from app.core.role_catalog import user_has_any_role_type


class CurrentUser:
    def __init__(
        self,
        username: str,
        role_ids: list[int] | None = None,
        matched_roles: list[dict[str, Any]] | None = None,
        is_group_admin: bool = False,
        has_app_access: bool = False,
        app_role: dict[str, Any] | None = None,
        display_role: str | None = None,
    ) -> None:
        self.username = username
        self.role_ids = role_ids or []
        self.matched_roles = matched_roles or []
        self.is_group_admin = is_group_admin
        self.has_app_access = has_app_access
        self.app_role = app_role
        self.display_role = display_role


def _parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _verify_token_to_iam_user(token: str) -> dict[str, Any] | None:
    if not settings.iam_api_base_url:
        return None
    url = settings.iam_api_base_url.rstrip("/") + "/v1/verify-user"
    request = Request(url, method="POST", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_debug_role_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def get_current_user(
    authorization: str | None = Header(default=None),
    x_username: str | None = Header(default=None, alias=DEBUG_USERNAME_HEADER),
    x_role_ids: str | None = Header(default=None, alias=DEBUG_ROLE_IDS_HEADER),
    db: Session = Depends(get_primary_db),
) -> CurrentUser:
    if settings.app_debug and x_username:
        payload = {
            "username": x_username,
            "rolesdivision": [{"role_id": role_id} for role_id in _parse_debug_role_ids(x_role_ids)],
        }
        session = resolve_auth_session(db, payload)
        return CurrentUser(
            username=x_username,
            role_ids=session["division_role_ids"],
            matched_roles=session["matched_roles"],
            is_group_admin=session["is_group_admin"],
            has_app_access=session["has_access"],
            app_role=session["app_role"],
            display_role=session["display_role"],
        )

    token = _parse_bearer_token(authorization)
    if not token:
        raise AppException("Authorization bearer token is required", status_code=401, code=UNAUTHORIZED)

    verify_payload = _verify_token_to_iam_user(token)
    if not verify_payload:
        raise AppException("Invalid IAM token", status_code=401, code=UNAUTHORIZED)

    identity = extract_verify_identity(verify_payload)
    session = resolve_auth_session(db, verify_payload)
    if not session["has_access"]:
        raise AppException("User does not have access to this application", status_code=403, code=FORBIDDEN)

    return CurrentUser(
        username=str(identity.get("username") or "").strip() or "unknown",
        role_ids=session["division_role_ids"],
        matched_roles=session["matched_roles"],
        is_group_admin=session["is_group_admin"],
        has_app_access=session["has_access"],
        app_role=session["app_role"],
        display_role=session["display_role"],
    )


def require_group_admin(user: CurrentUser) -> None:
    if not user.is_group_admin:
        raise AppException("Group admin access is required", status_code=403, code=FORBIDDEN)


def require_role_types(*role_types: str):
    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.is_group_admin:
            return user
        if not user_has_any_role_type(user, *role_types):
            raise AppException("Insufficient role", status_code=403, code=FORBIDDEN)
        return user

    return _checker
