import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.common.exception.business_exception import BusinessException
from app.common.response.api_response import ApiResponse

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI):

    # 业务异常
    @app.exception_handler(BusinessException)
    async def business_exception_handler(
        request: Request,
        exc: BusinessException,
    ):

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

        logger.exception(
            "Unhandled exception occurred",
            exc_info=exc,
        )

        return JSONResponse(
            status_code=500,
            content=ApiResponse.error(
                code="INTERNAL_ERROR",
                message="Internal server error",
            ).model_dump(),
        )
