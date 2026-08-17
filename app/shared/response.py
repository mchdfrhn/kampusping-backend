from typing import Any, Generic, TypeVar

from fastapi import status
from pydantic import BaseModel
from starlette.responses import JSONResponse

DataT = TypeVar("DataT")


class ApiResponse(BaseModel, Generic[DataT]):
    success: bool = True
    message: str = "Success"
    data: DataT | None = None


def success_response(
    data: Any | None = None,
    *,
    message: str = "Success",
    status_code: int = status.HTTP_200_OK,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiResponse[Any](success=True, message=message, data=data).model_dump(),
    )
