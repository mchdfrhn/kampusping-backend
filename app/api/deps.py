from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CSRF_HEADER, FORBIDDEN, UNAUTHORIZED
from app.core.exceptions import AppException
from app.core.security import decode_access_token
from app.db.session import get_db
from app.modules.users.model import User
from app.modules.users.repository import UserRepository

DbSession = Annotated[AsyncSession, Depends(get_db)]

bearer_scheme = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    if credentials is None:
        raise AppException(
            "Autentikasi diperlukan. Silakan masuk.", status_code=401, code=UNAUTHORIZED
        )
    user = await UserRepository(db).get(decode_access_token(credentials.credentials))
    if user is None or not user.is_active:
        raise AppException(
            "Autentikasi diperlukan. Silakan masuk.", status_code=401, code=UNAUTHORIZED
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permissions(*codes: str):
    """Dependency factory: the user must hold *every* listed permission, otherwise 403.

    Authorization is enforced here on the server; hiding UI elements is only a convenience.
    """

    async def checker(user: CurrentUser) -> User:
        missing = set(codes) - user.permission_codes
        if missing:
            raise AppException(
                "Anda tidak memiliki izin untuk melakukan tindakan ini.",
                status_code=403,
                code=FORBIDDEN,
                details={"required": sorted(missing)},
            )
        return user

    return checker


def require_csrf_header(request: Request) -> None:
    """Cookie-authenticated endpoints must carry the custom header (see ``CSRF_HEADER``)."""
    if not request.headers.get(CSRF_HEADER):
        raise AppException(
            f"Header wajib tidak ditemukan: {CSRF_HEADER}.", status_code=403, code=FORBIDDEN
        )
