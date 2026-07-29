from openai import AsyncOpenAI

from app.core.config import settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.schemas import ChatRequest, ChatResponse


class OpenAIProvider(BaseChatProvider):
    """OpenAI 聊天模型 Provider 的预留实现。"""

    MODEL_NAME = "openai"

    def __init__(self):
        """根据配置创建 OpenAI 兼容客户端。"""

        self.client = AsyncOpenAI(
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url,
        )

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """预留聊天入口，完成实现前主动拒绝调用。"""

        raise NotImplementedError

    def _build_messages(
        self,
        request: ChatRequest,
    ) -> list[dict]:
        """将内部消息模型转换为 OpenAI 兼容字典。"""
        return [
            {
                "role": message.role.value,
                "content": message.content,
            }
            for message in request.messages
        ]
