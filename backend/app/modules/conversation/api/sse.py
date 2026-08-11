from collections.abc import AsyncIterator

from app.modules.conversation.schema.message_schema import (
    MessageStreamEvent,
)


def encode_sse_event(event: MessageStreamEvent) -> str:
    """把一个 Service 业务事件编码为标准 SSE 文本帧。"""
    # Pydantic JSON 会把正文换行编码为 \n，确保每个 data 字段只占一行。
    return f"event: {event.type.value}\ndata: {event.model_dump_json()}\n\n"


async def close_message_stream(
    stream: AsyncIterator[MessageStreamEvent],
) -> None:
    """在迭代器支持 aclose 时主动关闭，释放 Provider 和网络流资源。"""
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


async def iter_sse_frames(
    first_event: MessageStreamEvent,
    remaining_events: AsyncIterator[MessageStreamEvent],
) -> AsyncIterator[str]:
    """先发送已预取事件，再顺序编码剩余事件，并保证最终关闭业务流。"""
    try:
        yield encode_sse_event(first_event)
        async for event in remaining_events:
            yield encode_sse_event(event)
    finally:
        # 正常结束、编码异常和客户端断开都会经过这里。
        await close_message_stream(remaining_events)
