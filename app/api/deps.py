from app.db.multi_session import get_session
from app.db.session import get_db_session, get_sync_db_session


def get_primary_db():
    """Primary application database (roles catalog, menu mapping, business tables)."""
    yield from get_session("PRIMARY")


def get_iam_db():
    yield from get_session("IAM")


def get_reference_db():
    yield from get_session("REFERENCE")


__all__ = [
    "get_db_session",
    "get_sync_db_session",
    "get_primary_db",
    "get_iam_db",
    "get_reference_db",
]
