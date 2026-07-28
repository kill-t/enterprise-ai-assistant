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
    ) -> ChatRequest:
        """构造与 Provider 无关的聊天请求。"""
        messages: list[ChatMessage] = []

        # 会话未自定义系统提示词时使用项目默认模板。
        prompt = system_prompt or PromptTemplate.chat()
        messages.append(ChatMessage(role=ChatRole.SYSTEM, content=prompt))
        messages.extend(history)
        messages.append(ChatMessage(role=ChatRole.USER, content=user_message))

        return ChatRequest(messages=messages, temperature=temperature)
