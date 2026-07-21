from openai import OpenAI

from app.core.config import settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.schemas import ChatRequest, ChatResponse


class OpenAIProvider(BaseChatProvider):

    MODEL_NAME = "openai"

    def __init__(self):

        self.client = OpenAI(
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url,
        )

    def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:

        raise NotImplementedError

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
