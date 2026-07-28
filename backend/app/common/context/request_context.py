from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


def set_request_id(request_id: str) -> None:
    """将当前请求标识写入异步上下文。"""
    request_id_context.set(request_id)


def get_request_id() -> str | None:
    """读取当前异步上下文中的请求标识。"""
    return request_id_context.get()
