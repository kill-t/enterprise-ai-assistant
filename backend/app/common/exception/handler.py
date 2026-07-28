import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.common.exception.business_exception import BusinessException
from app.common.response.api_response import ApiResponse

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI):
    """注册业务异常与未知异常的统一 JSON 响应处理器。"""

    # 业务异常
    @app.exception_handler(BusinessException)
    async def business_exception_handler(
        request: Request,
        exc: BusinessException,
    ):
        """将预期内的业务异常转换为稳定错误码和 HTTP 状态。"""

        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.error(
                code=exc.code.value,
                message=exc.message,
            ).model_dump(),
        )

    # 未知异常
    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request,
        exc: Exception,
    ):
        """记录未处理异常，并向客户端隐藏内部实现细节。"""

        logger.exception(
            "发生未处理异常",
            exc_info=exc,
        )

        return JSONResponse(
            status_code=500,
            content=ApiResponse.error(
                code="INTERNAL_ERROR",
                message="服务器内部错误",
            ).model_dump(),
        )
