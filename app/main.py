import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.common.handlers import register_exception_handlers
from app.router import register_routers
from app.core.config import settings
from app.core.logging import setup_logging
from app.modules.system.router import router as system_router
from app.modules.conversation.api.conversation_router import (
    router as conversation_router,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    logger.info("Enterprise AI Assistant starting...")

    yield

    logger.info("Enterprise AI Assistant stopped.")


def create_app() -> FastAPI:
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

    # 注册系统模块
    register_exception_handlers(app)
    register_routers(app)

    return app


app = create_app()
