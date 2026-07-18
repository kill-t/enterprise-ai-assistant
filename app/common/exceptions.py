class BusinessException(Exception):
    """
    所有业务异常的基类
    """

    def __init__(
        self,
        code: int,
        message: str,
    ):
        self.code = code
        self.message = message