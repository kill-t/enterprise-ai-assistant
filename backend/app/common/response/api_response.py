from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一 API 响应结构。"""

    code: str
    message: str
    data: T | None = None

    @classmethod
    def success(
        cls,
        data: T,
        message: str = "success",
    ):
        """构造包含业务数据的成功响应。"""
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
        """构造不包含业务数据的错误响应。"""
        return cls(
            code=code,
            message=message,
            data=None,
        )
