# Migration Notes

This starter intentionally defines no business models and ships with no generated business migration.

Alembic reads `DATABASE_URL` from `app.core.config.settings`, so switching databases is done through `.env` rather than by editing `alembic.ini`.

Keep the core migrations database-neutral. Future projects should avoid database-specific indexes, storage engines, shard keys, sort keys, or vendor-specific column types unless the project explicitly requires them.

For SingleStore, schema and migration behavior can differ from PostgreSQL and MySQL. Test generated revisions against the target SingleStore environment before release.
