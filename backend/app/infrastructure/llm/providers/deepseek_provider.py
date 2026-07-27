from openai import OpenAI
import time

from app.core.loggers import llm_logger
from app.core.config import settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.schemas import ChatRequest, ChatResponse


class DeepSeekProvider(BaseChatProvider):

    PROVIDER_NAME = "deepseek"

    def __init__(self):

        self.client = OpenAI(
            api_key=settings.deepseek.api_key,
            base_url=settings.deepseek.base_url,
        )

    def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        start = time.perf_counter()

        response = self.client.chat.completions.create(
            model=settings.deepseek.model,
            messages=self._build_messages(request),
            temperature=request.temperature,
            stream=False,
        )

        elapsed = time.perf_counter() - start
        llm_logger.info(
            (
                "provider=%s "
                "model=%s "
                "prompt_tokens=%s "
                "completion_tokens=%s "
                "total_tokens=%s "
                "latency=%.3fs"
            ),
            self.PROVIDER_NAME,
            response.model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.usage.total_tokens,
            elapsed,
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
