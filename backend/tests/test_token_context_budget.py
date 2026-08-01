import pytest
from pathlib import Path
from types import SimpleNamespace

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.config import ConversationMemorySettings, ModelContextSettings
from app.infrastructure.llm.context_manager import (
    ContextBudgetExceededError,
    ContextManager,
)
from app.infrastructure.llm.schemas import ChatMessage, ChatRole
from app.infrastructure.llm.token_counter import TokenCounter
from app.infrastructure.llm.prompt_builder import PromptBuilder
from app.modules.conversation.service.conversation_service import ConversationService


class CharacterTokenCounter:
    """用字符数提供稳定测试结果，避免测试依赖第三方 tokenizer 版本。"""

    def count_messages(self, messages: list[ChatMessage]) -> int:
        """把消息正文字符数相加，便于精确安排测试预算。"""
        return sum(len(message.content) for message in messages)


class FakeEncoder:
    """模拟模型专用 tokenizer，每个空格分隔项视为一个 token。"""

    def encode(self, text: str) -> list[int]:
        """返回与单词数量相同的虚拟 token 编号。"""
        return list(range(len(text.split())))


def message(role: ChatRole, content: str) -> ChatMessage:
    """简化测试消息创建。"""
    return ChatMessage(role=role, content=content)


def build_context_manager(
    context_window_tokens: int = 30,
    max_output_tokens: int = 5,
    token_safety_margin: int = 5,
) -> ContextManager:
    """构造使用确定性计数器的上下文管理器。"""
    return ContextManager(
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        token_safety_margin=token_safety_margin,
        token_counter_factory=lambda model_name: CharacterTokenCounter(),
    )


def test_token_counter_uses_injected_model_encoder() -> None:
    """存在模型 tokenizer 时，应使用其编码结果而不是字符数。"""
    counter = TokenCounter(model_name="known-model", encoder=FakeEncoder())

    assert counter.count("one two three") == 3
    assert counter.uses_model_tokenizer is True


def test_tokenizer_load_failure_falls_back_without_breaking_chat(monkeypatch) -> None:
    """编码表缺失或下载失败时，应自动降级而不是让聊天失败。"""
    import tiktoken

    def raise_network_error(model_name: str):
        """模拟 tokenizer 首次加载编码表时网络不可用。"""
        raise OSError(f"无法加载 {model_name} 的编码表")

    monkeypatch.setattr(tiktoken, "encoding_for_model", raise_network_error)
    counter = TokenCounter(model_name="gpt-4o")

    assert counter.count("你好，Enterprise AI Assistant") > 0
    assert counter.uses_model_tokenizer is False


def test_unknown_model_uses_conservative_utf8_estimate() -> None:
    """未知模型应按 UTF-8 字节保守估算，中英文都不能直接使用字符数。"""
    counter = TokenCounter(model_name="unknown-model", encoder=False)

    assert counter.count("hello") == 2
    assert counter.count("你好") == 2
    assert counter.uses_model_tokenizer is False


def test_context_keeps_newest_complete_turns_within_input_budget() -> None:
    """预算不足时只保留最新完整轮次，并忽略孤立或未完成消息。"""
    manager = build_context_manager()
    history = [
        message(ChatRole.ASSISTANT, "孤立回答"),
        message(ChatRole.USER, "旧问题一"),
        message(ChatRole.ASSISTANT, "旧回答一"),
        message(ChatRole.USER, "新问题"),
        message(ChatRole.ASSISTANT, "新回答"),
        message(ChatRole.USER, "未回答问题"),
    ]

    selected = manager.build_history(
        history=history,
        system_prompt="系统词",
        user_message="当前问题",
        model_name="mock",
    )

    assert [(item.role, item.content) for item in selected] == [
        (ChatRole.USER, "新问题"),
        (ChatRole.ASSISTANT, "新回答"),
    ]


def test_context_never_keeps_half_of_an_oversized_newest_turn() -> None:
    """最新轮次整体放不下时，不得只留下其中一条消息。"""
    manager = build_context_manager(context_window_tokens=20)
    history = [
        message(ChatRole.USER, "短问题"),
        message(ChatRole.ASSISTANT, "短回答"),
        message(ChatRole.USER, "这是一个很长的新问题"),
        message(ChatRole.ASSISTANT, "这是一个很长的新回答"),
    ]

    selected = manager.build_history(
        history=history,
        system_prompt="系统词",
        user_message="当前问题",
        model_name="mock",
    )

    assert selected == []


def test_system_and_current_message_are_reserved_before_history() -> None:
    """系统提示词和当前问题自身超过输入预算时，应拒绝而不是截断正文。"""
    manager = build_context_manager(context_window_tokens=15)

    with pytest.raises(ContextBudgetExceededError):
        manager.build_history(
            history=[],
            system_prompt="很长的系统提示词",
            user_message="必须完整保留的当前问题",
            model_name="mock",
        )


def test_available_input_budget_reserves_output_and_safety_margin() -> None:
    """可用输入预算必须扣除输出预留和安全余量。"""
    manager = build_context_manager(
        context_window_tokens=100,
        max_output_tokens=20,
        token_safety_margin=10,
    )

    assert manager.available_input_tokens == 70


def test_model_context_settings_exposes_same_available_input_budget() -> None:
    """后台摘要和前台聊天必须复用相同的输入预算计算公式。"""
    context = ModelContextSettings(
        context_window_tokens=100,
        max_output_tokens=20,
        token_safety_margin=10,
    )

    assert context.available_input_tokens == 70


def test_conversation_memory_has_positive_summary_batch_default() -> None:
    """摘要批次必须拥有独立正数预算，不能把全部积压历史一次发送给模型。"""
    memory = ConversationMemorySettings()

    assert memory.summary_batch_tokens == 4000


def test_summary_batch_budget_is_documented_in_environment_template() -> None:
    """新增配置必须同步到环境变量模板，避免部署时只能依赖隐藏默认值。"""
    environment_template = Path(".env.example").read_text(encoding="utf-8")

    assert "CONVERSATION_MEMORY__SUMMARY_BATCH_TOKENS=4000" in environment_template


def test_worker_retry_backoff_defaults_are_ordered() -> None:
    """摘要重试基础延迟必须小于等于最大退避，避免无效配置。"""
    from app.core.config import WorkerSettings

    worker = WorkerSettings()

    assert worker.summary_retry_base_delay_seconds == 30
    assert worker.summary_retry_max_delay_seconds == 300


def test_service_maps_required_content_overflow_to_business_error() -> None:
    """必选内容超限时，Service 应返回稳定错误码而不是泄露内部异常。"""
    service = object.__new__(ConversationService)
    service.prompt_builder = PromptBuilder()
    service.context_manager = build_context_manager(context_window_tokens=15)
    conversation = SimpleNamespace(
        system_prompt="很长的系统提示词",
        model_name="mock",
        temperature=0.7,
    )

    with pytest.raises(BusinessException) as error:
        service._build_chat_request(
            conversation=conversation,
            history=[],
            user_message="必须完整保留的当前问题",
        )

    assert error.value.code == ErrorCode.CONTEXT_WINDOW_EXCEEDED
    assert error.value.status_code == 422
