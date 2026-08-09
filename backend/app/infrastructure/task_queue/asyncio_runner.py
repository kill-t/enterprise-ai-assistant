"""为同步任务入口提供跨平台的异步协程运行方式。"""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar


ResultT = TypeVar("ResultT")
EventLoopFactory = Callable[[], asyncio.AbstractEventLoop]


def get_event_loop_factory(
    platform_name: str | None = None,
) -> EventLoopFactory | None:
    """Windows 返回 psycopg 兼容的 Selector 循环，其余平台沿用默认循环。"""
    current_platform = platform_name or sys.platform
    if current_platform.startswith("win"):
        return asyncio.SelectorEventLoop
    return None


def run_async(
    coroutine: Coroutine[Any, Any, ResultT],
    *,
    platform_name: str | None = None,
) -> ResultT:
    """在 Celery 等同步入口中运行协程，并确保事件循环最终被关闭。"""
    loop_factory = get_event_loop_factory(platform_name)
    if loop_factory is None:
        return asyncio.run(coroutine)

    # Windows 默认 Proactor 循环不受 psycopg 异步驱动支持，因此显式创建 Selector 循环。
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(coroutine)
