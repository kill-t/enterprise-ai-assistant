from datetime import datetime, timezone

from pydantic import ValidationError

from app.modules.conversation.schema.conversation_summary_schema import (
    ConversationSummaryContent,
)
from app.modules.conversation.service.conversation_summary_processor import (
    ConversationSummaryPromptBudgetExceededError,
    ConversationSummaryRangeError,
)
from app.modules.conversation.service.summary_failure_policy import (
    SummaryFailurePolicy,
)


def test_timeout_is_retryable_and_uses_exponential_backoff() -> None:
    """模型超时属于临时错误，连续失败时退避时间应逐步增加并受上限约束。"""
    policy = SummaryFailurePolicy(base_delay_seconds=30, max_delay_seconds=100)
    now = datetime.now(timezone.utc)

    decision = policy.classify(TimeoutError("模型超时"))

    assert decision.retryable is True
    assert decision.error_code == "SUMMARY_PROVIDER_TIMEOUT"
    assert policy.next_retry_at(attempts=1, now=now).timestamp() - now.timestamp() == 30
    assert policy.next_retry_at(attempts=2, now=now).timestamp() - now.timestamp() == 60
    assert policy.next_retry_at(attempts=3, now=now).timestamp() - now.timestamp() == 100


def test_invalid_model_structure_is_retryable_without_leaking_validation_error() -> None:
    """偶发结构错误可以重试，但数据库只保存稳定错误信息。"""
    try:
        ConversationSummaryContent.model_validate(
            {"user_goal_and_context": "只有一个栏目"}
        )
    except ValidationError as exc:
        decision = SummaryFailurePolicy().classify(exc)

    assert decision.retryable is True
    assert decision.error_code == "SUMMARY_INVALID_MODEL_OUTPUT"
    assert "Field required" not in decision.error_message


def test_range_and_prompt_budget_errors_are_permanent() -> None:
    """数据边界丢失和确定超限不会随时间恢复，不得自动重试。"""
    policy = SummaryFailurePolicy()

    range_decision = policy.classify(
        ConversationSummaryRangeError("冻结目标不存在")
    )
    budget_decision = policy.classify(
        ConversationSummaryPromptBudgetExceededError("输入超限")
    )

    assert range_decision.retryable is False
    assert range_decision.error_code == "SUMMARY_RANGE_INVALID"
    assert budget_decision.retryable is False
    assert budget_decision.error_code == "SUMMARY_PROMPT_TOO_LARGE"


def test_unknown_programming_error_is_not_retried_forever() -> None:
    """未知代码错误默认永久失败，避免部署缺陷形成高频重试风暴。"""
    decision = SummaryFailurePolicy().classify(RuntimeError("内部变量错误"))

    assert decision.retryable is False
    assert decision.error_code == "SUMMARY_UNEXPECTED_ERROR"
    assert "内部变量错误" not in decision.error_message
