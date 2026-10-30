# Adding a module

Copy `app/modules/example/` and rename. Example: a `products` module.

1. **Model** (`model.py`): subclass `AuditedModel` for `id`, audit columns and soft delete.
   Set `__tablename__`, add your columns, and import the model in `app/db/models/__init__.py`.
2. **Migration**: `alembic revision -m "create products"`, then write `op.create_table(...)` using
   `id_column()` and `audit_columns()` from `app/db/migration_helpers.py`. Implement `downgrade()`.
3. **Constants** (`constants.py`): statuses and limits for the module; never inline them in routers.
4. **Schema** (`schema.py`): Pydantic models for input and output. Output models use
   `ConfigDict(from_attributes=True)`.
5. **Repository** (`repository.py`): subclass `BaseRepository[Product]`, declare `sort_fields`
   (the only columns clients may sort by) and write queries on `self.select()`, which already
   excludes soft-deleted rows. Use `like_pattern()` for search.
6. **Service** (`service.py`): business rules, uniqueness checks, `commit()`. Pass the signed-in
   user in to fill `created_by` / `updated_by` / `deleted_by`.
7. **Permissions**: add `PRODUCTS_READ`, `PRODUCTS_CREATE`, ... to `app/core/permissions.py`
   (and to `PERMISSIONS`), then run `python -m app.cli seed`.
8. **Router** (`router.py`): keep it thin. Guard each route with `require_permissions(...)`,
   accept `ListQuery` for list endpoints and return `Page[...]`.
9. **Register** the router in `app/api/v1/router.py`.
10. **Tests**: copy `tests/test_example.py`; cover 401/403, validation, uniqueness, filters and
    soft delete.

The frontend counterpart is `src/modules/example/` in the web app.
