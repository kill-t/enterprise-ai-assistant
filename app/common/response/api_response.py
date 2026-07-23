from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    code: str
    message: str
    data: T | None = None

    @classmethod
    def success(
        cls,
        data: T,
        message: str = "success",
    ):
        return cls(
            code="SUCCESS",
            message=message,
            data=data,
        )

    @classmethod
    def error(
        cls,
        code: str,
        message: str,
    ):
        return cls(
            code=code,
            message=message,
            data=None,
        )
