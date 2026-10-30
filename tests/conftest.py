from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.permissions import ADMIN_ROLE, MEMBER_ROLE
from app.db.base import Base
from app.db.seed import seed_reference_data
from app.db.session import get_engine, get_session_factory
from app.main import create_app

from .helpers import auth_headers, create_user


@pytest.fixture(autouse=True)
async def database() -> None:
    """A fresh, seeded schema for every test."""
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with get_session_factory()() as db:
        await seed_reference_data(db)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    await create_user("admin@example.com", ADMIN_ROLE)
    return await auth_headers(client, "admin@example.com")


@pytest.fixture
async def member_headers(client: httpx.AsyncClient) -> dict[str, str]:
    await create_user("member@example.com", MEMBER_ROLE)
    return await auth_headers(client, "member@example.com")
