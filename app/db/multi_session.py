from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

ENGINES: dict[str, Engine] = {}
SESSIONS: dict[str, sessionmaker[Session]] = {}
_initialized = False

_MYSQL_DIALECTS = frozenset({"mysql", "mariadb", "singlestoredb"})


def _register_session_timezone(engine: Engine) -> None:
    if engine.dialect.name not in _MYSQL_DIALECTS:
        return

    @event.listens_for(engine, "connect")
    def _set_session_timezone(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("SET time_zone = '+07:00'")
        cursor.close()


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    for key, url in settings.database_urls.items():
        engine = create_engine(url, pool_pre_ping=True)
        _register_session_timezone(engine)
        ENGINES[key] = engine
        SESSIONS[key] = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _initialized = True


def get_session(db_key: str) -> Generator[Session, None, None]:
    _ensure_initialized()
    session_factory = SESSIONS.get(db_key)
    if session_factory is None:
        raise RuntimeError(
            f"Database session '{db_key}' is not configured. Set DB_URL_{db_key} or DATABASE_URL."
        )
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
