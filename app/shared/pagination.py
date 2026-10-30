from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel

from app.core.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

T = TypeVar("T")


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class ListParams:
    """List contract: ``page`` (1-based), ``page_size``, ``search``, ``sort_by``, ``sort_order``."""

    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    search: str | None = None
    sort_by: str | None = None
    sort_order: SortOrder = SortOrder.DESC

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def get_list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100)] = None,
    sort_by: Annotated[str | None, Query(max_length=50)] = None,
    sort_order: SortOrder = SortOrder.DESC,
) -> ListParams:
    return ListParams(
        page=page,
        page_size=page_size,
        search=search.strip() if search and search.strip() else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )


ListQuery = Annotated[ListParams, Depends(get_list_params)]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
