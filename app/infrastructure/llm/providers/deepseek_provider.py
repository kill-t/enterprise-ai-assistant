from openai import OpenAI

from app.core.config import settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.schemas import ChatRequest, ChatResponse


class DeepSeekProvider(BaseChatProvider):

    MODEL_NAME = "deepseek"

    def __init__(self):

        self.client = OpenAI(
            api_key=settings.deepseek.api_key,
            base_url=settings.deepseek.base_url,
        )

    def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:

        response = self.client.chat.completions.create(
            model=settings.deepseek.model,
            messages=self._build_messages(request),
            temperature=request.temperature,
            stream=False,
        )

        return ChatResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

    def _build_messages(
        self,
        request: ChatRequest,
    ) -> list[dict]:
        return [
            {
                "role": message.role.value,
                "content": message.content,
            }
            for message in request.messages
        ]
