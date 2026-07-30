from app.infrastructure.llm.prompt_template import PromptTemplate
from app.infrastructure.llm.schemas import ChatMessage, ChatRequest, ChatRole


class PromptBuilder:
    """按固定顺序组装系统提示词、历史消息和当前问题。"""

    def build(
        self,
        system_prompt: str | None,
        history: list[ChatMessage],
        user_message: str,
        temperature: float = 0.7,
        model_name: str | None = None,
    ) -> ChatRequest:
        """构造与 Provider 无关的聊天请求。"""
        messages: list[ChatMessage] = []

        # 会话未自定义系统提示词时使用项目默认模板。
        prompt = self.resolve_system_prompt(system_prompt)
        messages.append(ChatMessage(role=ChatRole.SYSTEM, content=prompt))
        messages.extend(history)
        messages.append(ChatMessage(role=ChatRole.USER, content=user_message))

        return ChatRequest(
            messages=messages,
            temperature=temperature,
            model=model_name,
        )

    @staticmethod
    def resolve_system_prompt(system_prompt: str | None) -> str:
        """统一解析自定义或默认系统提示词，保证预算计算与最终请求一致。"""
        return system_prompt or PromptTemplate.chat()
