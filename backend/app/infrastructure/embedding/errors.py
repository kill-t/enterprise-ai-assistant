from enum import Enum


class EmbeddingErrorCode(str, Enum):
    """供 Worker 稳定分类、记录和重试的 Embedding 错误码。"""

    INVALID_INPUT = "EMBEDDING_INVALID_INPUT"
    CONFIGURATION_INVALID = "EMBEDDING_CONFIGURATION_INVALID"
    PROVIDER_REQUEST_FAILED = "EMBEDDING_PROVIDER_REQUEST_FAILED"
    PROVIDER_RESPONSE_INVALID = "EMBEDDING_PROVIDER_RESPONSE_INVALID"
    DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"


class EmbeddingError(Exception):
    """封装不会泄露第三方响应正文和密钥的安全错误信息。"""

    def __init__(self, code: EmbeddingErrorCode, message: str) -> None:
        """保存稳定错误码和可记录的中文提示。"""
        self.code = code
        self.message = message
        super().__init__(message)
