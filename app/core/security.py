"""Password hashing, JWT access tokens and opaque refresh tokens."""

import asyncio
import hashlib
import re
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import AfterValidator

from app.shared.utils import utc_now

from .config import settings
from .constants import UNAUTHORIZED
from .exceptions import AppException

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - token type label, not a secret
MAX_PASSWORD_LENGTH = 128

_hasher = PasswordHasher()
# Verified against when the account does not exist, so response time does not leak it.
DUMMY_PASSWORD_HASH = _hasher.hash("not-a-real-password")


# --- Passwords ---------------------------------------------------------------------------


def check_password_strength(value: str) -> str:
    if len(value) < settings.password_min_length:
        raise ValueError(f"Password must be at least {settings.password_min_length} characters.")
    if len(value) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must contain at least one letter and one digit.")
    return value


StrongPassword = Annotated[str, AfterValidator(check_password_strength)]


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


# Argon2 is CPU-bound; keep it off the event loop.
async def hash_password_async(plain: str) -> str:
    return await asyncio.to_thread(hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_password, plain, hashed)


# --- Access tokens (JWT) -----------------------------------------------------------------


def create_access_token(user_id: int) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``."""
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    now = utc_now()
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "jti": uuid4().hex,
        "iat": now,
        "exp": now + expires_delta,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> int:
    """Return the user id from a valid access token, otherwise raise a 401."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "type"]},
        )
        if payload["type"] != ACCESS_TOKEN_TYPE:
            raise jwt.InvalidTokenError("Unexpected token type")
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AppException(
            "Invalid or expired access token.", status_code=401, code=UNAUTHORIZED
        ) from exc


# --- Refresh tokens (opaque, stored hashed) -----------------------------------------------


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``; only the hash is persisted."""
    raw_token = secrets.token_urlsafe(48)
    return raw_token, hash_token(raw_token)
