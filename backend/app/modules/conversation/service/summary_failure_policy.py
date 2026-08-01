from dataclasses import dataclass
from datetime import datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

from app.modules.conversation.service.conversation_summary_processor import (
    ConversationSummaryConflictError,
    ConversationSummaryPromptBudgetExceededError,
    ConversationSummaryRangeError,
)


@dataclass(frozen=True)
class SummaryFailureDecision:
    """保存异常分类后的稳定错误信息和是否允许重试。"""

    retryable: bool
    error_code: str
    error_message: str


class SummaryFailurePolicy:
    """把底层异常转换为安全业务错误，并计算指数退避时间。"""

    def __init__(
        self,
        base_delay_seconds: int = 30,
        max_delay_seconds: int = 300,
    ) -> None:
        """配置第一次重试延迟和指数退避最大值。"""
        if base_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("摘要重试延迟必须大于零")
        if base_delay_seconds > max_delay_seconds:
            raise ValueError("摘要重试基础延迟不能大于最大退避时间")
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds

    def classify(self, exc: Exception) -> SummaryFailureDecision:
        """按照是否可能随时间恢复，将异常分成临时错误和永久错误。"""
        if isinstance(exc, (TimeoutError, APITimeoutError, SoftTimeLimitExceeded)):
            return SummaryFailureDecision(
                retryable=True,
                error_code="SUMMARY_PROVIDER_TIMEOUT",
                error_message="摘要模型调用超时",
            )
        if isinstance(exc, APIConnectionError):
            return SummaryFailureDecision(
                retryable=True,
                error_code="SUMMARY_PROVIDER_UNAVAILABLE",
                error_message="摘要模型暂时不可用",
            )
        if isinstance(exc, RateLimitError):
            return SummaryFailureDecision(
                retryable=True,
                error_code="SUMMARY_PROVIDER_RATE_LIMITED",
                error_message="摘要模型请求受到限流",
            )
        if isinstance(exc, APIStatusError):
            return SummaryFailureDecision(
                retryable=exc.status_code >= 500,
                error_code=(
                    "SUMMARY_PROVIDER_UNAVAILABLE"
                    if exc.status_code >= 500
                    else "SUMMARY_PROVIDER_REQUEST_REJECTED"
                ),
                error_message=(
                    "摘要模型暂时不可用"
                    if exc.status_code >= 500
                    else "摘要模型拒绝了当前请求"
                ),
            )
        if isinstance(exc, ValidationError):
            return SummaryFailureDecision(
                retryable=True,
                error_code="SUMMARY_INVALID_MODEL_OUTPUT",
                error_message="摘要模型返回结构无效",
            )
        if isinstance(exc, ConversationSummaryConflictError):
            return SummaryFailureDecision(
                retryable=True,
                error_code="SUMMARY_VERSION_CONFLICT",
                error_message="摘要版本发生冲突",
            )
        if isinstance(exc, ConversationSummaryRangeError):
            return SummaryFailureDecision(
                retryable=False,
                error_code="SUMMARY_RANGE_INVALID",
                error_message="摘要消息范围不完整",
            )
        if isinstance(exc, ConversationSummaryPromptBudgetExceededError):
            return SummaryFailureDecision(
                retryable=False,
                error_code="SUMMARY_PROMPT_TOO_LARGE",
                error_message="摘要输入超过模型上下文限制",
            )
        return SummaryFailureDecision(
            retryable=False,
            error_code="SUMMARY_UNEXPECTED_ERROR",
            error_message="会话摘要任务处理失败",
        )

    def next_retry_at(self, attempts: int, now: datetime) -> datetime:
        """按 2 的指数增长计算下一次执行时间，并限制最大退避。"""
        delay_seconds = min(
            self.base_delay_seconds * (2 ** max(attempts - 1, 0)),
            self.max_delay_seconds,
        )
        return now + timedelta(seconds=delay_seconds)
