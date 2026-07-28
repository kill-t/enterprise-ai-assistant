import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.router import register_routers
from app.core.config import settings
from app.core.logger import setup_logging
from app.modules.conversation.api.conversation_router import (
    router as conversation_router,
)
from app.common.exception.handler import register_exception_handlers
from app.middleware.request_id import RequestIdMiddleware
from app.infrastructure.database.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("Enterprise AI Assistant starting...")

    try:
        yield
    finally:
        await engine.dispose()
        logger.info("Enterprise AI Assistant stopped.")


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    # app.include_router(
    #     conversation_router,
    #     prefix="/api/v1",
    # )

    app.include_router(conversation_router)
    app.add_middleware(RequestIdMiddleware)

    # 注册系统模块
    register_exception_handlers(app)
    register_routers(app)

    return app


app = create_app()
