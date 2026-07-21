from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRequest,
    ChatRole,
)
from app.infrastructure.llm.prompt_template import PromptTemplate


class PromptBuilder:

    def build(
        self,
        system_prompt: str | None,
        history: list[ChatMessage],
        user_message: str,
        temperature: float = 0.7,
    ) -> ChatRequest:

        messages: list[ChatMessage] = []

        # 使用默认 Prompt
        prompt = system_prompt or PromptTemplate.chat()

        messages.append(
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=prompt,
            )
        )

        messages.extend(history)

        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=user_message,
            )
        )

        return ChatRequest(
            messages=messages,
            temperature=temperature,
        )
