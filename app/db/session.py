from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import settings

async_engine: AsyncEngine | None = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None
sync_engine: Engine | None = None
sync_session_factory: sessionmaker[Session] | None = None

if settings.is_async_database_url:
    async_engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    async_session_factory = async_sessionmaker(
        bind=async_engine,
        expire_on_commit=False,
        autoflush=False,
    )
else:
    sync_engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
    )
    sync_session_factory = sessionmaker(
        bind=sync_engine,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Request-scoped async database session dependency."""
    if async_session_factory is None:
        raise RuntimeError(
            "The configured DATABASE_URL does not use an async SQLAlchemy driver. "
            "Use get_sync_db_session for sync drivers or configure an async URL."
        )

    async with async_session_factory() as session:
        yield session


def get_sync_db_session() -> Generator[Session, None, None]:
    """Request-scoped sync database session dependency for sync-only dialects."""
    if sync_session_factory is None:
        raise RuntimeError(
            "The configured DATABASE_URL uses an async SQLAlchemy driver. "
            "Use get_db_session instead."
        )

    with sync_session_factory() as session:
        yield session
