from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI

from app.core import security
from app.core.permissions import ADMIN_ROLE, PIMPINAN_ROLE, STAF_KEUANGAN_ROLE
from app.db.base import Base
from app.db.seed import seed_reference_data
from app.db.session import get_engine, get_session_factory
from app.main import create_app
from app.modules.auth import service as auth_service

from .helpers import auth_headers, create_user


@pytest.fixture(scope="session")
def production_hasher() -> PasswordHasher:
    """The argon2 hasher exactly as the application builds it (before any test swap)."""
    return security._hasher


@pytest.fixture(scope="session", autouse=True)
def cheap_password_hashing(production_hasher: PasswordHasher) -> Iterator[None]:
    """Hash passwords with minimal argon2 cost in tests only (the suite hashes in every test).

    Production parameters live in ``app.core.security`` and are untouched; a test asserts them.
    """
    cheap = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    original_dummy = auth_service.DUMMY_PASSWORD_HASH
    security._hasher = cheap
    auth_service.DUMMY_PASSWORD_HASH = cheap.hash("not-a-real-password")
    yield
    security._hasher = production_hasher
    auth_service.DUMMY_PASSWORD_HASH = original_dummy


@pytest.fixture(autouse=True)
async def database() -> None:
    """A fresh, seeded schema for every test."""
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with get_session_factory()() as db:
        await seed_reference_data(db)


@pytest.fixture
def app() -> FastAPI:
    """The application under test; tests may set ``app.dependency_overrides`` on it."""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    await create_user("admin@example.com", ADMIN_ROLE)
    return await auth_headers(client, "admin@example.com")


@pytest.fixture
async def staf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    await create_user("staf@example.com", STAF_KEUANGAN_ROLE)
    return await auth_headers(client, "staf@example.com")


@pytest.fixture
async def pimpinan_headers(client: httpx.AsyncClient) -> dict[str, str]:
    await create_user("pimpinan@example.com", PIMPINAN_ROLE)
    return await auth_headers(client, "pimpinan@example.com")
