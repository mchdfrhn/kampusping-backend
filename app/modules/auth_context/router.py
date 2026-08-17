from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.deps import get_primary_db
from app.core.auth import _parse_bearer_token, _verify_token_to_iam_user
from app.core.auth_session import resolve_auth_session
from app.core.constants import UNAUTHORIZED
from app.core.exceptions import AppException

router = APIRouter()


@router.get("/auth/session")
def get_auth_session(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_primary_db),
):
    token = _parse_bearer_token(authorization)
    if not token:
        raise AppException("Authorization bearer token is required", status_code=401, code=UNAUTHORIZED)

    verify_payload = _verify_token_to_iam_user(token)
    if not verify_payload:
        raise AppException("Invalid IAM token", status_code=401, code=UNAUTHORIZED)

    return resolve_auth_session(db, verify_payload)
