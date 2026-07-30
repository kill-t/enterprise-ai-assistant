from redis.asyncio import Redis

from app.infrastructure.memory.base import BaseMemory
from app.infrastructure.memory.schemas import MemoryMessage


class RedisMemory(BaseMemory):
    """使用 Redis List 保存最近消息的短期记忆实现。"""

    def __init__(
        self,
        redis: Redis,
        max_messages: int = 20,
    ):
        """保存 Redis 客户端和单个会话最多保留的消息数量。"""
        self.redis = redis
        self.max_messages = max_messages

    def _key(
        self,
        conversation_id: str,
    ) -> str:
        """生成带命名空间的会话记忆键，避免与其他缓存冲突。"""

        return f"memory:conversation:{conversation_id}"

    async def add_message(
        self,
        conversation_id: str,
        message: MemoryMessage,
    ) -> None:
        """追加消息，并裁剪列表长度、刷新过期时间。"""

        key = self._key(conversation_id)

        await self.redis.rpush(
            key,
            message.model_dump_json(),
        )

        # 只保留最近 N 条，防止长对话无限占用 Redis 内存。
        await self.redis.ltrim(
            key,
            -self.max_messages,
            -1,
        )

        # 每次活跃对话都会续期 24 小时。
        await self.redis.expire(
            key,
            60 * 60 * 24,
        )

    async def get_messages(
        self,
        conversation_id: str,
    ) -> list[MemoryMessage]:
        """读取并反序列化指定会话的全部短期记忆。"""

        key = self._key(conversation_id)

        values = await self.redis.lrange(
            key,
            0,
            -1,
        )

        return [MemoryMessage.model_validate_json(item) for item in values]

    async def delete_messages(self, conversation_id: str) -> None:
        """删除会话对应的 Redis List 及其过期时间。"""
        await self.redis.delete(self._key(conversation_id))
