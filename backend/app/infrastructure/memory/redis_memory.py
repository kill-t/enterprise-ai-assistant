from redis.asyncio import Redis

from app.infrastructure.memory.base import BaseMemory
from app.infrastructure.memory.schemas import MemoryMessage


class RedisMemory(BaseMemory):

    def __init__(
        self,
        redis: Redis,
        max_messages: int = 20,
    ):
        self.redis = redis
        self.max_messages = max_messages

    def _key(
        self,
        conversation_id: str,
    ) -> str:

        return f"memory:conversation:{conversation_id}"

    async def add_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ) -> None:

        key = self._key(conversation_id)

        await self.redis.rpush(
            key,
            message.model_dump_json(),
        )

        # 保留最近N条
        await self.redis.ltrim(
            key,
            -self.max_messages,
            -1,
        )

        # 设置过期时间
        await self.redis.expire(
            key,
            60 * 60 * 24,
        )

    async def get_messages(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:

        key = self._key(conversation_id)

        values = await self.redis.lrange(
            key,
            0,
            -1,
        )

        return [MemoryMessage.model_validate_json(item) for item in values]
