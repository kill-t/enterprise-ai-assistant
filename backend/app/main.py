import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.router import register_routers
from app.core.config import settings
from app.core.logger import setup_logging
from app.common.exception.handler import register_exception_handlers
from app.middleware.request_id import RequestIdMiddleware
from app.infrastructure.database.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用启动与关闭生命周期，并在关闭时释放数据库连接池。"""

    # 配置缺失应在服务刚启动时失败，不能拖延到用户第一次点击登录。
    settings.auth.validate_runtime()
    settings.rag.validate_runtime(deepseek_api_key=settings.deepseek.api_key)
    logger.info("Enterprise AI Assistant 正在启动")

    try:
        yield
    finally:
        await engine.dispose()
        logger.info("Enterprise AI Assistant 已停止")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    setup_logging()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)

    # 异常处理器和模块路由必须在应用启动前完成注册。
    register_exception_handlers(app)
    register_routers(app)

    return app


app = create_app()
