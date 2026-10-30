"""Keep Alembic migrations portable across PostgreSQL, MySQL, SQLite and SingleStore."""

import sqlalchemy as sa

from app.core.config import DatabaseEngine, settings

# SQLite only auto-increments INTEGER primary keys.
BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def id_column() -> sa.Column:
    return sa.Column("id", BIGINT_PK, primary_key=True, autoincrement=True)


def audit_columns() -> list[sa.Column]:
    """created/updated/deleted audit trail. Rows are soft-deleted, never removed."""
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(255)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_by", sa.String(255)),
    ]


def foreign_keys(*constraints: sa.ForeignKeyConstraint) -> list[sa.ForeignKeyConstraint]:
    """SingleStore does not support FOREIGN KEY constraints, so omit them there."""
    if settings.database_engine == DatabaseEngine.SINGLESTORE:
        return []
    return list(constraints)
