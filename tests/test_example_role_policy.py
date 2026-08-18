"""Contoh unit test modul bisnis — opsional namun disarankan jika timeline memungkinkan."""

from typing import cast
from unittest.mock import MagicMock

from app.core.role_catalog import ROLE_TYPE_ADMIN, ROLE_TYPE_SUPER_ADMIN, RoleCatalog
from app.db.models.reference import AppAllowedRole


def _role(iam_role_id: int, role_type: str) -> AppAllowedRole:
    row = AppAllowedRole(
        iam_role_id=iam_role_id,
        display_name=f"Role {iam_role_id}",
        auth_slug=f"slug_{iam_role_id}",
        role_type=role_type,
        sort_order=0,
        is_active=1,
    )
    row.id = iam_role_id
    return row


def test_role_catalog_iam_ids_for_type() -> None:
    rows = (
        _role(1001, ROLE_TYPE_SUPER_ADMIN),
        _role(1002, ROLE_TYPE_ADMIN),
        _role(1003, ROLE_TYPE_ADMIN),
    )
    catalog = RoleCatalog(rows=rows)

    assert catalog.iam_ids_for_type(ROLE_TYPE_SUPER_ADMIN) == [1001]
    assert catalog.iam_ids_for_type(ROLE_TYPE_ADMIN) == [1002, 1003]
    assert catalog.slug_for_iam_id(1002) == "slug_1002"
    assert catalog.role_type_for_iam_id(1001) == ROLE_TYPE_SUPER_ADMIN


def test_role_catalog_from_db_uses_repository(monkeypatch) -> None:
    mock_db = MagicMock()
    fake_rows = [_role(2001, ROLE_TYPE_ADMIN)]

    def fake_list_active(_db):
        assert _db is mock_db
        return fake_rows

    monkeypatch.setattr(
        "app.core.role_catalog.list_active_app_allowed_roles",
        fake_list_active,
    )

    catalog = RoleCatalog.from_db(mock_db)
    assert catalog.iam_ids_for_type(ROLE_TYPE_ADMIN) == [2001]


def test_example_constants_allowed_statuses() -> None:
    from app.modules.example.constants import ALLOWED_STATUSES, STATUS_ACTIVE

    statuses = cast(tuple[str, ...], ALLOWED_STATUSES)
    assert STATUS_ACTIVE in statuses
    assert len(statuses) == 3
