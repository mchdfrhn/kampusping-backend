from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, require_csrf_header
from app.core.config import settings
from app.core.exceptions import AppException, error_response
from app.core.rate_limit import limiter
from app.modules.users.schema import CurrentUserRead

from .schema import ChangePasswordRequest, LoginRequest, ProfileUpdate, TokenResponse
from .service import AuthService, IssuedSession

router = APIRouter(prefix="/auth", tags=["Auth"])

RefreshCookie = Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        path=settings.refresh_cookie_path,  # only sent to /auth/*, never with ordinary API calls
        domain=settings.cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _token_response(response: Response, session: IssuedSession) -> TokenResponse:
    _set_refresh_cookie(response, session.refresh_token)
    return TokenResponse(
        access_token=session.access_token,
        expires_in=session.expires_in,
        user=CurrentUserRead.from_user(session.user),
    )


@router.post("/login", response_model=TokenResponse, summary="Sign in with email and password")
@limiter.limit(settings.login_rate_limit)
async def login(
    request: Request, response: Response, payload: LoginRequest, db: DbSession
) -> TokenResponse:
    session = await AuthService(db).login(
        payload.email,
        payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return _token_response(response, session)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh cookie and get a new access token",
    dependencies=[Depends(require_csrf_header)],
)
async def refresh(
    request: Request, response: Response, db: DbSession, refresh_token: RefreshCookie = None
) -> TokenResponse | JSONResponse:
    try:
        session = await AuthService(db).refresh(
            refresh_token,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AppException as exc:
        # A dead session: answer with the standard error and drop the useless cookie.
        failure = error_response(exc.status_code, exc.code, exc.message)
        _clear_refresh_cookie(failure)
        return failure
    return _token_response(response, session)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current refresh token chain",
    dependencies=[Depends(require_csrf_header)],
)
async def logout(db: DbSession, refresh_token: RefreshCookie = None) -> Response:
    await AuthService(db).logout(refresh_token)
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(result)
    return result


@router.get("/me", response_model=CurrentUserRead, summary="Current user and permissions")
async def me(user: CurrentUser) -> CurrentUserRead:
    return CurrentUserRead.from_user(user)


@router.patch("/me", response_model=CurrentUserRead, summary="Update own profile")
async def update_me(payload: ProfileUpdate, user: CurrentUser, db: DbSession) -> CurrentUserRead:
    updated = await AuthService(db).update_profile(user, payload.full_name)
    return CurrentUserRead.from_user(updated)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change own password (signs out every session)",
)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, db: DbSession
) -> Response:
    await AuthService(db).change_password(user, payload.current_password, payload.new_password)
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(result)
    return result
