"""Domain constants for the example module. Statuses live here, never inline in the router."""

from typing import Literal

STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"

ExampleStatus = Literal["draft", "active", "inactive"]
ALLOWED_STATUSES: tuple[str, ...] = (STATUS_DRAFT, STATUS_ACTIVE, STATUS_INACTIVE)

EXPORT_ROW_LIMIT = 10_000
