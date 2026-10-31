"""JSON form of validated rows, kept on the batch between preview and apply."""

from dataclasses import asdict
from datetime import date
from typing import Any

from .validation import ValidRow


def row_to_json(row: ValidRow) -> dict[str, Any]:
    data = asdict(row)
    data["due_date"] = row.due_date.isoformat()
    data["paid_at"] = row.paid_at.isoformat() if row.paid_at else None
    return data


def row_from_json(data: dict[str, Any]) -> ValidRow:
    return ValidRow(
        **{
            **data,
            "due_date": date.fromisoformat(data["due_date"]),
            "paid_at": date.fromisoformat(data["paid_at"]) if data.get("paid_at") else None,
        }
    )
