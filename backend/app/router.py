from fastapi import FastAPI

from app.modules.system.router import router as system_router


def register_routers(app: FastAPI) -> None:
    """将各业务模块的路由统一注册到 FastAPI 应用。"""
    app.include_router(system_router)
