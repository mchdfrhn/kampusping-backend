"""Change log written while an import is applied; rollback reads it back.

Only the tracked fields below are logged. ``opted_out_*`` and ``exempt_*`` are not: an import never
touches them (only staff change consent and exemptions).
"""

from datetime import date, datetime
from typing import Any

from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

from .constants import ChangeAction, ChangeEntity
from .model import ImportChange

TRACKED_FIELDS: dict[ChangeEntity, tuple[str, ...]] = {
    ChangeEntity.STUDENT: (
        "name",
        "study_program",
        "cohort",
        "phone",
        "phone_status",
        "deleted_at",
        "deleted_by",
    ),
    ChangeEntity.INVOICE: (
        "semester",
        "amount",
        "due_date",
        "status",
        "paid_at",
        "paid_via",
        "source",
        "deleted_at",
        "deleted_by",
    ),
}

_DATE_FIELDS = frozenset({"due_date", "paid_at"})
_DATETIME_FIELDS = frozenset({"deleted_at"})


def entity_of(obj: Student | Invoice) -> ChangeEntity:
    return ChangeEntity.STUDENT if isinstance(obj, Student) else ChangeEntity.INVOICE


def _to_json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date | datetime) else value


def serialize_fields(entity: ChangeEntity, obj: Student | Invoice) -> dict[str, Any]:
    """The tracked fields of ``obj`` as JSON-safe values."""
    return {field: _to_json(getattr(obj, field)) for field in TRACKED_FIELDS[entity]}


def deserialize_value(field: str, value: Any) -> Any:
    """Inverse of the serialisation in :func:`serialize_fields` for one field."""
    if value is None:
        return None
    if field in _DATE_FIELDS:
        return date.fromisoformat(value)
    if field in _DATETIME_FIELDS:
        return datetime.fromisoformat(value)
    return value


class ChangeRecorder:
    """Collects what an apply creates or changes.

    Call :meth:`snapshot` for every existing row *before* it is mutated (the first call per object
    wins) and :meth:`created` for every new row; call :meth:`build` once after the final flush so
    new rows have their ids. Rows whose tracked fields did not change produce no entry.
    """

    def __init__(self) -> None:
        self._before: dict[int, tuple[ChangeEntity, Student | Invoice, dict[str, Any]]] = {}
        self._created: list[Student | Invoice] = []

    def snapshot(self, obj: Student | Invoice) -> None:
        if id(obj) in self._before:
            return
        entity = entity_of(obj)
        self._before[id(obj)] = (entity, obj, serialize_fields(entity, obj))

    def created(self, obj: Student | Invoice) -> None:
        self._created.append(obj)

    def build(self, *, batch_id: int, actor: str, now: datetime) -> list[ImportChange]:
        changes: list[ImportChange] = []

        def entry(
            entity: ChangeEntity,
            obj: Student | Invoice,
            action: ChangeAction,
            before: dict[str, Any] | None,
            after: dict[str, Any],
        ) -> ImportChange:
            return ImportChange(
                batch_id=batch_id,
                entity=entity.value,
                entity_id=obj.id,
                action=action.value,
                before=before,
                after=after,
                created_by=actor,
                created_at=now,
                updated_by=actor,
                updated_at=now,
            )

        for obj in self._created:
            entity = entity_of(obj)
            changes.append(
                entry(entity, obj, ChangeAction.CREATED, None, serialize_fields(entity, obj))
            )
        for entity, obj, before in self._before.values():
            current = serialize_fields(entity, obj)
            changed = [field for field in before if before[field] != current[field]]
            if changed:
                changes.append(
                    entry(
                        entity,
                        obj,
                        ChangeAction.UPDATED,
                        {field: before[field] for field in changed},
                        {field: current[field] for field in changed},
                    )
                )
        return changes
