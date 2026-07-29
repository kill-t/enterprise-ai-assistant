from fastapi import FastAPI

from app.modules.conversation.api.conversation_router import (
    router as conversation_router,
)
from app.modules.identity.api.auth_router import router as auth_router
from app.modules.system.router import router as system_router


def register_routers(app: FastAPI) -> None:
    """将各业务模块的路由统一注册到 FastAPI 应用。"""
    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(conversation_router)
