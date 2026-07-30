from collections.abc import Callable
from typing import Protocol

from app.infrastructure.llm.schemas import ChatMessage, ChatRole
from app.infrastructure.llm.token_counter import TokenCounter


class MessageTokenCounter(Protocol):
    """上下文裁剪所依赖的最小计数接口。"""

    def count_messages(self, messages: list[ChatMessage]) -> int:
        """返回一组聊天消息的估算 token 数。"""
        ...


class ContextBudgetExceededError(ValueError):
    """系统提示词与当前问题已经超过可用输入预算。"""


class ContextManager:
    """先为模型输出预留空间，再按完整轮次选择最近历史。"""

    def __init__(
        self,
        context_window_tokens: int = 8192,
        max_output_tokens: int = 1024,
        token_safety_margin: int = 256,
        token_counter_factory: Callable[[str], MessageTokenCounter] = TokenCounter,
    ) -> None:
        """配置模型上下文窗口、输出预留、安全余量和计数器工厂。"""
        if max_output_tokens + token_safety_margin >= context_window_tokens:
            raise ValueError("输出预留与安全余量必须小于模型上下文窗口")

        self.context_window_tokens = context_window_tokens
        self.max_output_tokens = max_output_tokens
        self.token_safety_margin = token_safety_margin
        self.token_counter_factory = token_counter_factory

    @property
    def available_input_tokens(self) -> int:
        """返回本次输入可使用的最大 token 数。"""
        return (
            self.context_window_tokens
            - self.max_output_tokens
            - self.token_safety_margin
        )

    def build_history(
        self,
        history: list[ChatMessage],
        system_prompt: str,
        user_message: str,
        model_name: str,
    ) -> list[ChatMessage]:
        """保留必选消息，并在剩余预算内选择最新的完整问答轮次。"""
        counter = self.token_counter_factory(model_name)
        required_messages = [
            ChatMessage(role=ChatRole.SYSTEM, content=system_prompt),
            ChatMessage(role=ChatRole.USER, content=user_message),
        ]
        required_tokens = counter.count_messages(required_messages)
        if required_tokens > self.available_input_tokens:
            raise ContextBudgetExceededError(
                "系统提示词与当前问题超过模型可用输入预算"
            )

        history_budget = self.available_input_tokens - required_tokens
        selected_turns: list[list[ChatMessage]] = []
        selected_tokens = 0

        # 从最新轮次向前选择，保证预算不足时优先保留最相关的近期上下文。
        for turn in reversed(self._group_complete_turns(history)):
            turn_tokens = counter.count_messages(turn)
            if selected_tokens + turn_tokens > history_budget:
                break
            selected_turns.append(turn)
            selected_tokens += turn_tokens

        selected_turns.reverse()
        return [message for turn in selected_turns for message in turn]

    @staticmethod
    def _group_complete_turns(
        history: list[ChatMessage],
    ) -> list[list[ChatMessage]]:
        """把历史整理为以 user 开始、以 assistant 结束的完整轮次。"""
        complete_turns: list[list[ChatMessage]] = []
        current_turn: list[ChatMessage] = []

        for message in history:
            if message.role == ChatRole.USER:
                # 新问题出现时，上一组只有以助手回答结束才算完整。
                if current_turn and current_turn[-1].role == ChatRole.ASSISTANT:
                    complete_turns.append(current_turn)
                current_turn = [message]
                continue

            if current_turn:
                current_turn.append(message)

        if current_turn and current_turn[-1].role == ChatRole.ASSISTANT:
            complete_turns.append(current_turn)

        return complete_turns
