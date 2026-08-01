import json

from app.infrastructure.llm.base import BaseChatProvider

from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
    ChatResponseFormat,
)


class MockProvider(BaseChatProvider):
    """返回固定响应的测试 Provider，不访问外部模型服务。"""

    MODEL_NAME = "mock"

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """异步生成可预测的模拟聊天响应与 token 用量。"""

        if (
            request.response_format == ChatResponseFormat.JSON_OBJECT
            and request.metadata.get("operation") == "conversation_summary"
        ):
            # 本地开发不访问真实模型，但仍返回能通过同一 Pydantic 校验的摘要结构。
            content = json.dumps(
                {
                    "user_goal_and_context": "本地模拟会话目标",
                    "confirmed_facts_and_decisions": "暂无已确认事实",
                    "completed_items": "暂无已完成事项",
                    "open_questions_and_todos": "暂无待办",
                    "useful_preferences": "暂无明确偏好",
                },
                ensure_ascii=False,
            )
            return ChatResponse(
                content=content,
                model=self.MODEL_NAME,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            )

        return ChatResponse(
            content="Hello, I'm Enterprise AI Assistant.",
            model=self.MODEL_NAME,
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        )
