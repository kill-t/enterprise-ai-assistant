from dataclasses import dataclass
from datetime import datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded

from app.infrastructure.embedding.errors import EmbeddingError, EmbeddingErrorCode
from app.modules.knowledge.processing.errors import DocumentParseError


class DocumentSourceUnavailableError(Exception):
    """表示 MinIO 暂时无法读取原文件，稍后可能恢复。"""

    def __init__(self) -> None:
        """构造不泄露对象存储内部响应的安全异常。"""
        super().__init__("文档原文件暂时不可用")


class DocumentSourceIntegrityError(Exception):
    """表示下载内容与 PostgreSQL 保存的大小或哈希不一致。"""


class DocumentSourceNotFoundError(Exception):
    """表示任务关联文档不存在或已不处于可处理状态。"""


@dataclass(frozen=True, slots=True)
class DocumentIngestionFailureDecision:
    """保存失败是否可重试及可持久化的稳定错误信息。"""

    retryable: bool
    error_code: str
    error_message: str


class DocumentIngestionFailurePolicy:
    """把存储、解析和 Embedding 异常分类，并计算指数退避。"""

    def __init__(
        self,
        base_delay_seconds: int = 30,
        max_delay_seconds: int = 300,
    ) -> None:
        """配置第一次重试延迟和最大退避时间。"""
        if base_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("文档入库重试延迟必须大于零")
        if base_delay_seconds > max_delay_seconds:
            raise ValueError("文档入库重试基础延迟不能大于最大退避时间")
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds

    def classify(self, exc: Exception) -> DocumentIngestionFailureDecision:
        """把底层异常转换成安全、稳定且可驱动重试的业务结果。"""
        if isinstance(exc, (TimeoutError, SoftTimeLimitExceeded)):
            return DocumentIngestionFailureDecision(
                retryable=True,
                error_code="DOCUMENT_INGESTION_TIMEOUT",
                error_message="文档入库处理超时",
            )
        if isinstance(exc, DocumentSourceUnavailableError):
            return DocumentIngestionFailureDecision(
                retryable=True,
                error_code="DOCUMENT_SOURCE_UNAVAILABLE",
                error_message="文档原文件暂时不可用",
            )
        if isinstance(exc, DocumentSourceIntegrityError):
            return DocumentIngestionFailureDecision(
                retryable=False,
                error_code="DOCUMENT_SOURCE_INTEGRITY_MISMATCH",
                error_message="文档原文件与上传记录不一致",
            )
        if isinstance(exc, DocumentSourceNotFoundError):
            return DocumentIngestionFailureDecision(
                retryable=False,
                error_code="DOCUMENT_SOURCE_NOT_FOUND",
                error_message="文档入库来源不存在或状态无效",
            )
        if isinstance(exc, DocumentParseError):
            return DocumentIngestionFailureDecision(
                retryable=False,
                error_code=exc.code.value,
                error_message=exc.message,
            )
        if isinstance(exc, EmbeddingError):
            retryable_codes = {
                EmbeddingErrorCode.PROVIDER_REQUEST_FAILED,
                EmbeddingErrorCode.PROVIDER_RESPONSE_INVALID,
            }
            return DocumentIngestionFailureDecision(
                retryable=exc.code in retryable_codes,
                error_code=exc.code.value,
                error_message=exc.message,
            )
        return DocumentIngestionFailureDecision(
            retryable=False,
            error_code="DOCUMENT_INGESTION_UNEXPECTED_ERROR",
            error_message="文档入库处理失败",
        )

    def next_retry_at(self, attempts: int, now: datetime) -> datetime:
        """按当前执行次数计算有上限的指数退避时间。"""
        delay_seconds = min(
            self.base_delay_seconds * (2 ** max(attempts - 1, 0)),
            self.max_delay_seconds,
        )
        return now + timedelta(seconds=delay_seconds)
