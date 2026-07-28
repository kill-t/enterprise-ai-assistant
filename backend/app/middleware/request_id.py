import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.common.context.request_context import set_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求创建并回传唯一请求标识。"""

    async def dispatch(
        self,
        request: Request,
        call_next,
    ) -> Response:
        """在调用后续处理器前写入上下文，并在响应头返回标识。"""

        request_id = str(uuid.uuid4())

        set_request_id(request_id)

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id

        return response
