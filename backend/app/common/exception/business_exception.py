from app.common.exception.error_code import ErrorCode


class BusinessException(Exception):

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code

        super().__init__(message)
