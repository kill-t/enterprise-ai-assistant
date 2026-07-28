from app.common.exception.error_code import ErrorCode


class BusinessException(Exception):
    """可转换为稳定 API 错误响应的业务异常。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
    ):
        """保存错误码、用户可读信息和对应 HTTP 状态码。"""
        self.code = code
        self.message = message
        self.status_code = status_code

        super().__init__(message)
