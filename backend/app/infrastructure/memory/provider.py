from collections.abc import AsyncGenerator

from redis.asyncio import Redis

from app.core.config import settings
from app.infrastructure.memory.redis_memory import RedisMemory
from app.infrastructure.memory.memory_manager import MemoryManager


async def get_memory_manager() -> AsyncGenerator[MemoryManager, None]:
    """创建请求级 Redis Memory，并在请求结束后关闭连接。"""
    redis = Redis.from_url(
        settings.redis.url,
        decode_responses=True,
    )
    try:
        # 对业务层只暴露 MemoryManager，避免 Service 直接依赖 Redis。
        yield MemoryManager(
            short_term_memory=RedisMemory(
                redis=redis,
                max_messages=20,
            )
        )
    finally:
        await redis.aclose()
