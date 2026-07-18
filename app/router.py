from fastapi import FastAPI

from app.modules.system.router import router as system_router
# from app.modules.chat.router import router as chat_router


def register_routers(app: FastAPI) -> None:
    app.include_router(system_router)
    # app.include_router(chat_router)