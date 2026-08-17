import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.sql import literal_column

from alembic import context
from alembic.ddl.impl import DefaultImpl
from alembic.runtime.migration import HeadMaintainer


class SingleStoreImpl(DefaultImpl):
    __dialect__ = "singlestoredb"


def patch_singlestore_version_update() -> None:
    if getattr(HeadMaintainer, "_singlestore_delete_insert_patch", False):
        return

    def _update_version_delete_insert(self: HeadMaintainer, from_: str, to_: str) -> None:
        assert to_ not in self.heads
        self.heads.remove(from_)
        self.heads.add(to_)
        ret = self.context.impl._exec(
            self.context._version.delete().where(
                self.context._version.c.version_num == literal_column("'%s'" % from_)
            )
        )
        if (
            not self.context.as_sql
            and self.context.dialect.supports_sane_rowcount
            and ret is not None
            and ret.rowcount != 1
        ):
            raise RuntimeError(
                "Online migration expected to match one row when deleting "
                f"'{from_}' in '{self.context.version_table}'; {ret.rowcount} found"
            )
        self.context.impl._exec(
            self.context._version.insert().values(version_num=literal_column("'%s'" % to_))
        )

    HeadMaintainer._update_version = _update_version_delete_insert  # type: ignore[method-assign]
    HeadMaintainer._singlestore_delete_insert_patch = True  # type: ignore[attr-defined]


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.db.models  # noqa: F401, E402

if settings.database_engine.value == "singlestore":
    try:
        import sqlalchemy_singlestoredb  # noqa: F401
    except ImportError:
        pass
    patch_singlestore_version_update()

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def is_async_url(url: str) -> bool:
    return any(marker in url for marker in ("+asyncpg", "+asyncmy", "+aiomysql", "+aiosqlite"))


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_sync_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif is_async_url(settings.database_url):
    asyncio.run(run_async_migrations())
else:
    run_sync_migrations()
