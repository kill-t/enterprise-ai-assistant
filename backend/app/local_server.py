"""提供 Windows 兼容的本地 FastAPI 启动命令。"""

import asyncio
import sys
from collections.abc import Callable

import uvicorn


EventLoopFactory = Callable[[], asyncio.AbstractEventLoop]


def get_event_loop_factory(
    platform_name: str | None = None,
) -> EventLoopFactory | None:
    """Windows 使用 Selector 事件循环，兼容 psycopg 异步连接。"""
    current_platform = platform_name or sys.platform
    if current_platform.startswith("win"):
        return asyncio.SelectorEventLoop
    return None


def run_local_server() -> None:
    """启动本地开发服务；生产环境继续使用容器或标准 Uvicorn 命令。"""
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    loop_factory = get_event_loop_factory()

    if loop_factory is None:
        asyncio.run(server.serve())
        return

    # Uvicorn.run 会自行创建默认循环；这里显式控制循环才能避开 Windows Proactor 限制。
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    run_local_server()
