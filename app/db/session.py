from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Created lazily so importing the app never opens a connection."""
    kwargs: dict[str, Any] = {"echo": settings.database_echo}
    if settings.database_url.startswith("sqlite"):
        if ":memory:" in settings.database_url:
            kwargs.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        kwargs["pool_pre_ping"] = True
    return create_async_engine(settings.database_url, **kwargs)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Request-scoped session. Services commit explicitly; uncommitted work is rolled back."""
    async with get_session_factory()() as session:
        yield session
