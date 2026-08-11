import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.common.dependencies import get_conversation_service
from app.common.enums import MessageRole, MessageStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.main import create_app
from app.modules.conversation.api.sse import encode_sse_event
from app.modules.conversation.schema.message_schema import (
    MessageResponse,
    MessageStreamCompletedEvent,
    MessageStreamDeltaEvent,
    MessageStreamErrorEvent,
    MessageStreamSourcesEvent,
    MessageStreamStartedEvent,
)
from app.modules.identity.dependencies import get_current_user


def build_message(
    role: MessageRole,
    status: MessageStatus,
    content: str = "",
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> MessageResponse:
    """创建 SSE 测试使用的稳定消息响应。"""
    now = datetime.now(timezone.utc)
    return MessageResponse(
        id=uuid4(),
        role=role,
        content=content,
        model_name="mock-stream" if role == MessageRole.ASSISTANT else "user",
        status=status,
        error_code=error_code,
        error_message=error_message,
        created_at=now,
        finished_at=now if status != MessageStatus.PENDING else None,
    )


def build_success_events():
    """构造 started、两个 delta 和 completed 的完整业务事件序列。"""
    user_message = build_message(
        MessageRole.USER,
        MessageStatus.COMPLETED,
        "整理会议纪要",
    )
    pending_message = build_message(
        MessageRole.ASSISTANT,
        MessageStatus.PENDING,
    )
    completed_message = pending_message.model_copy(
        update={
            "content": "会议完成",
            "status": MessageStatus.COMPLETED,
            "finished_at": datetime.now(timezone.utc),
        }
    )
    return [
        MessageStreamStartedEvent(
            user_message=user_message,
            assistant_message=pending_message,
        ),
        MessageStreamDeltaEvent(
            message_id=pending_message.id,
            delta="会议",
        ),
        MessageStreamDeltaEvent(
            message_id=pending_message.id,
            delta="完成",
        ),
        MessageStreamCompletedEvent(message=completed_message),
    ]


class FakeStreamingConversationService:
    """记录路由参数，并按给定顺序产生 Service 业务事件。"""

    def __init__(self, events) -> None:
        """保存事件脚本和可观察状态。"""
        self.events = events
        self.received = None
        self.closed = False

    async def stream_message(self, user_id, conversation_id, content):
        """逐项产生事件，并在响应结束或关闭时记录资源释放。"""
        self.received = (user_id, conversation_id, content)
        try:
            for event in self.events:
                yield event
        finally:
            self.closed = True


class FailingPreflightService:
    """在第一个 started 事件前抛出业务异常。"""

    async def stream_message(self, user_id, conversation_id, content):
        """模拟所有权校验失败，同时保持异步生成器接口。"""
        for event in ():
            yield event
        raise BusinessException(
            code=ErrorCode.CONVERSATION_NOT_FOUND,
            message="会话不存在",
            status_code=404,
        )


def parse_sse_frames(body: str) -> list[tuple[str, dict]]:
    """把测试响应拆成 event 名称与 JSON data，不承担生产解析职责。"""
    frames = []
    for raw_frame in body.strip().split("\n\n"):
        lines = raw_frame.splitlines()
        event_name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        frames.append((event_name, data))
    return frames


def test_sse_encoder_uses_event_field_and_single_line_json_data() -> None:
    """编码器必须生成标准空行分隔帧，并保留中文 JSON。"""
    event = MessageStreamDeltaEvent(message_id=uuid4(), delta="会议\n完成")

    frame = encode_sse_event(event)

    assert frame.startswith("event: delta\n")
    assert frame.endswith("\n\n")
    assert "data: " in frame
    assert "会议\\n完成" in frame
    assert len(frame.rstrip("\n").splitlines()) == 2


def test_stream_route_returns_ordered_sse_and_forwards_identity() -> None:
    """HTTP 路由应保留 Service 事件顺序、认证用户和请求参数。"""
    service = FakeStreamingConversationService(build_success_events())
    current_user = SimpleNamespace(id=uuid4())
    conversation_id = uuid4()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_conversation_service] = lambda: service

    async def scenario():
        """请求完整 SSE 响应并返回 HTTPX Response。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/stream",
                json={"content": "整理会议纪要"},
            )

    response = asyncio.run(scenario())
    frames = parse_sse_frames(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert [event_name for event_name, _ in frames] == [
        "started",
        "delta",
        "delta",
        "completed",
    ]
    assert frames[1][1]["delta"] == "会议"
    assert frames[-1][1]["message"]["content"] == "会议完成"
    assert service.received == (
        current_user.id,
        conversation_id,
        "整理会议纪要",
    )
    assert service.closed is True


def test_http_sse_exposes_sources_between_started_and_delta() -> None:
    """公共 HTTP SSE 应按 started、sources、delta、completed 顺序发送来源。"""
    events = build_success_events()
    assistant_message = events[0].assistant_message
    events.insert(
        1,
        MessageStreamSourcesEvent(
            message_id=assistant_message.id,
            sources=[],
        ),
    )
    service = FakeStreamingConversationService(events)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_conversation_service] = lambda: service

    async def scenario():
        """请求带内部 sources 的业务流，并收集公开 HTTP 帧。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                f"/api/v1/conversations/{uuid4()}/messages/stream",
                json={"content": "整理会议纪要"},
            )

    response = asyncio.run(scenario())
    frames = parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [event_name for event_name, _ in frames] == [
        "started",
        "sources",
        "delta",
        "delta",
        "completed",
    ]
    assert frames[1][1] == {
        "type": "sources",
        "message_id": str(assistant_message.id),
        "sources": [],
    }
    assert service.closed is True


def test_stream_route_serializes_failed_terminal_event() -> None:
    """Service error 应作为正常 SSE 终点传输，而不是改写成第二个 HTTP 响应。"""
    events = build_success_events()[:2]
    failed_message = events[0].assistant_message.model_copy(
        update={
            "content": "会议",
            "status": MessageStatus.FAILED,
            "error_code": ErrorCode.MODEL_REQUEST_FAILED.value,
            "error_message": "模型流式调用失败，请稍后重试",
            "finished_at": datetime.now(timezone.utc),
        }
    )
    events.append(MessageStreamErrorEvent(message=failed_message))
    service = FakeStreamingConversationService(events)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_conversation_service] = lambda: service

    async def scenario():
        """请求以 error 结束的 SSE 响应。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                f"/api/v1/conversations/{uuid4()}/messages/stream",
                json={"content": "整理会议纪要"},
            )

    response = asyncio.run(scenario())
    frames = parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [event_name for event_name, _ in frames] == [
        "started",
        "delta",
        "error",
    ]
    assert frames[-1][1]["message"]["status"] == "failed"
    assert frames[-1][1]["message"]["error_code"] == (
        ErrorCode.MODEL_REQUEST_FAILED.value
    )


def test_preflight_business_error_remains_json_404_before_sse_starts() -> None:
    """started 前的业务异常必须由全局处理器返回 JSON 404，而不是错误的 SSE 200。"""
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_conversation_service] = lambda: (
        FailingPreflightService()
    )

    async def scenario():
        """请求在预取 started 时失败的流式接口。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                f"/api/v1/conversations/{uuid4()}/messages/stream",
                json={"content": "无权访问的会话"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == ErrorCode.CONVERSATION_NOT_FOUND.value


def test_stream_route_rejects_non_started_first_event_before_sse_starts() -> None:
    """第一项不是 started 时应返回 JSON 500，并立即关闭错误的业务流。"""
    service = FakeStreamingConversationService(
        [MessageStreamDeltaEvent(message_id=uuid4(), delta="错误片段")]
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_conversation_service] = lambda: service

    async def scenario():
        """请求违反 Service 流事件顺序契约的接口。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                f"/api/v1/conversations/{uuid4()}/messages/stream",
                json={"content": "测试错误事件顺序"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == ErrorCode.INTERNAL_ERROR.value
    assert service.closed is True


def test_openapi_exposes_authenticated_message_stream_endpoint() -> None:
    """OpenAPI 必须公开流式 POST 路径及 text/event-stream 响应类型。"""
    operation = create_app().openapi()["paths"][
        "/api/v1/conversations/{conversation_id}/messages/stream"
    ]["post"]

    assert operation["security"] == [{"OAuth2PasswordBearer": []}]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
