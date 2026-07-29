from app.common.exception.error_code import ErrorCode


class BusinessException(Exception):
    """可转换为稳定 API 错误响应的业务异常。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
        headers: dict[str, str] | None = None,
    ):
        """保存错误码、用户提示、HTTP 状态码和可选响应头。"""
        self.code = code
        self.message = message
        self.status_code = status_code
        self.headers = headers

        super().__init__(message)
