# Database

## Engines

| `DATABASE_ENGINE`      | URL scheme              | Notes                                             |
| ---------------------- | ----------------------- | ------------------------------------------------- |
| `postgresql` (default) | `postgresql+asyncpg://` | Recommended                                       |
| `mysql`                | `mysql+asyncmy://`      | `pip install asyncmy`                             |
| `singlestore`          | `mysql+asyncmy://`      | MySQL wire protocol; see below                    |
| `sqlite`               | `sqlite+aiosqlite://`   | Local tries and tests only; refused in production |

## Tables

- Primary key: `id BIGINT` auto-increment (an `INTEGER` alias on SQLite, which only
  auto-increments integers).
- Business tables inherit `AuditedModel`: `created_at/by`, `updated_at/by`, `deleted_at/by`.
  Rows are soft-deleted; `BaseRepository.select()` filters them out.
- Constraint and index names follow the naming convention in `app/db/base.py`, so Alembic
  autogenerate and manual migrations produce stable names.
- Unique columns (`users.email`, `roles.name`) also cover soft-deleted
  rows, so a deleted email or code cannot be reused. Services check this and return `409`.

### Import change log (migration 008)

`import_changes` records every student and invoice an applied import created (`action = created`,
`before` NULL) or changed (`action = updated`), with only the tracked fields that changed in
`before` / `after` (`app/modules/imports/changes.py`). `import_batches.has_change_log` is true for
batches applied after migration 008; batches applied earlier have no log and **cannot be rolled
back**. Rolling a batch back sets `import_batches.status = 'rolled_back'` and `rolled_back_at`.
The log holds personal data (name, phone), so keep retention in mind (UU PDP; not decided yet).
Migration 008 grants no permission: `imports:rollback` reaches `admin` through the seed only.

## Migrations

Write them by hand with `op.create_table`; the helpers keep them portable:

```python
from app.db.migration_helpers import audit_columns, foreign_keys, id_column

op.create_table(
    "widgets",
    id_column(),
    sa.Column("name", sa.String(100), nullable=False),
    *audit_columns(),
)
```

Always implement `downgrade()`. Verify with `alembic upgrade head && alembic downgrade base`.

## SingleStore

SingleStore is supported through its MySQL protocol, with these caveats:

- `foreign_keys(...)` omits FOREIGN KEY constraints because SingleStore does not support them.
- The generated tables use a plain `BIGINT AUTO_INCREMENT PRIMARY KEY`. `SHARD KEY`, `SORT KEY`
  and `AS SEQUENCE` are **not** generated. For large tables, write that DDL in a migration with
  `op.execute(...)` guarded by `settings.database_engine == DatabaseEngine.SINGLESTORE`.
- `alembic/env.py` patches Alembic's version-table update (delete + insert) for SingleStore.

This path is best-effort and has not been run against a live SingleStore cluster; test it before
relying on it.
