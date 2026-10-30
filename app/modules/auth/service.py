from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import UNAUTHORIZED
from app.core.exceptions import AppException
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password_async,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password_async,
)
from app.modules.users.model import User
from app.modules.users.repository import UserRepository
from app.shared.utils import ensure_utc, utc_now

from .model import RefreshToken
from .repository import RefreshTokenRepository


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    access_token: str
    expires_in: int
    refresh_token: str


def _invalid_credentials() -> AppException:
    # One message for unknown email, wrong password, inactive and locked accounts alike,
    # so the response cannot be used to enumerate accounts.
    return AppException("Invalid email or password.", status_code=401, code=UNAUTHORIZED)


def _invalid_session() -> AppException:
    return AppException(
        "Session expired. Please sign in again.", status_code=401, code=UNAUTHORIZED
    )


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)

    async def login(
        self, email: str, password: str, *, ip_address: str | None, user_agent: str | None
    ) -> IssuedSession:
        now = utc_now()
        user = await self.users.get_by_email(email)

        # Always run one Argon2 verification so timing does not reveal whether the email exists.
        password_ok = await verify_password_async(
            password, user.password_hash if user else DUMMY_PASSWORD_HASH
        )
        if user is None or not user.is_active:
            raise _invalid_credentials()
        if user.locked_until and ensure_utc(user.locked_until) > now:
            raise _invalid_credentials()

        if not password_ok:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.login_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_attempts = 0
            await self.db.commit()  # persist the counter before the error is raised
            raise _invalid_credentials()

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        if password_needs_rehash(user.password_hash):
            user.password_hash = await hash_password_async(password)

        session = await self._issue_session(
            user, family_id=str(uuid4()), ip_address=ip_address, user_agent=user_agent
        )
        await self.db.commit()
        return session

    async def refresh(
        self, raw_token: str | None, *, ip_address: str | None, user_agent: str | None
    ) -> IssuedSession:
        if not raw_token:
            raise _invalid_session()
        stored = await self.tokens.get_by_hash(hash_token(raw_token))
        if stored is None:
            raise _invalid_session()

        if stored.revoked_at is not None:
            # An already-rotated token was presented again: assume theft and end the whole chain.
            await self.tokens.revoke_family(stored.family_id)
            await self.db.commit()
            raise _invalid_session()
        if ensure_utc(stored.expires_at) <= utc_now():
            raise _invalid_session()

        user = await self.users.get(stored.user_id)
        if user is None or not user.is_active:
            await self.tokens.revoke_family(stored.family_id)
            await self.db.commit()
            raise _invalid_session()

        stored.revoked_at = utc_now()
        session = await self._issue_session(
            user, family_id=stored.family_id, ip_address=ip_address, user_agent=user_agent
        )
        await self.db.commit()
        return session

    async def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        stored = await self.tokens.get_by_hash(hash_token(raw_token))
        if stored is not None:
            await self.tokens.revoke_family(stored.family_id)
            await self.db.commit()

    async def change_password(self, user: User, current_password: str, new_password: str) -> None:
        if not await verify_password_async(current_password, user.password_hash):
            raise AppException(
                "Current password is incorrect.", status_code=400, code="invalid_password"
            )
        user.password_hash = await hash_password_async(new_password)
        user.password_changed_at = utc_now()
        user.updated_by = user.email
        await self.tokens.revoke_all_for_user(user.id)
        await self.db.commit()

    async def update_profile(self, user: User, full_name: str) -> User:
        user.full_name = full_name
        user.updated_by = user.email
        user.updated_at = utc_now()
        await self.db.commit()
        return user

    async def _issue_session(
        self, user: User, *, family_id: str, ip_address: str | None, user_agent: str | None
    ) -> IssuedSession:
        access_token, expires_in = create_access_token(user.id)
        raw_refresh, refresh_hash = new_refresh_token()
        await self.tokens.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id,
                token_hash=refresh_hash,
                expires_at=utc_now() + timedelta(days=settings.refresh_token_expire_days),
                user_agent=(user_agent or "")[:255] or None,
                ip_address=ip_address,
            )
        )
        return IssuedSession(
            user=user, access_token=access_token, expires_in=expires_in, refresh_token=raw_refresh
        )
