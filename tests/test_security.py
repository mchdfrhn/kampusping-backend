from datetime import timedelta

import jwt
import pytest
from argon2 import PasswordHasher

from app.core import security
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.security import (
    check_password_strength,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.shared.utils import like_pattern, utc_now


def test_production_hasher_keeps_the_default_argon2_parameters(
    production_hasher: PasswordHasher,
) -> None:
    # Tests run with a cheap hasher (see conftest); production must keep the strong defaults.
    assert production_hasher.time_cost == 3
    assert production_hasher.memory_cost == 65536
    assert production_hasher.parallelism == 4
    assert production_hasher.hash("Correct-horse-9").startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert production_hasher is not security._hasher  # the suite really runs on the cheap one


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("Correct-horse-9")

    assert hashed != "Correct-horse-9"
    assert verify_password("Correct-horse-9", hashed)
    assert not verify_password("wrong-password-1", hashed)
    assert not verify_password("anything", "not-a-valid-hash")
    assert not password_needs_rehash(hashed)


@pytest.mark.parametrize("weak", ["short1", "alllettersnodigits", "1234567890123"])
def test_weak_passwords_are_rejected(weak: str) -> None:
    with pytest.raises(ValueError, match="Kata sandi"):
        check_password_strength(weak)


def test_access_token_roundtrip() -> None:
    token, expires_in = create_access_token(42)

    assert decode_access_token(token) == 42
    assert expires_in == settings.access_token_expire_minutes * 60


def _forge(claims: dict, key: str = settings.secret_key, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


def _claims(**overrides: object) -> dict:
    now = utc_now()
    base = {"sub": "1", "type": "access", "iat": now, "exp": now + timedelta(minutes=5)}
    return {**base, **overrides}


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        _forge(_claims(exp=utc_now() - timedelta(minutes=1))),  # expired
        _forge(_claims(), key="a-different-secret-key-of-sufficient-length-000"),  # bad signature
        _forge(_claims(type="refresh")),  # wrong token type
        _forge({"sub": "1", "type": "access"}),  # missing exp/iat
        _forge(_claims(sub="abc")),  # non-numeric subject
    ],
)
def test_invalid_access_tokens_are_rejected(token: str) -> None:
    with pytest.raises(AppException) as error:
        decode_access_token(token)

    assert error.value.status_code == 401


def test_unsigned_token_is_rejected() -> None:
    unsigned = jwt.encode(_claims(), key=None, algorithm="none")

    with pytest.raises(AppException):
        decode_access_token(unsigned)


def test_refresh_tokens_are_random_and_stored_hashed() -> None:
    first, first_hash = new_refresh_token()
    second, _ = new_refresh_token()

    assert first != second
    assert first_hash == hash_token(first)
    assert first not in first_hash


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern("50%_off") == "%50\\%\\_off%"
