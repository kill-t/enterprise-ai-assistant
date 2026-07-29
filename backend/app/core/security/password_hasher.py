import asyncio

from pwdlib import PasswordHash


class PasswordHasher:
    """通过推荐的 Argon2 算法提供异步密码哈希和校验能力。"""

    def __init__(self) -> None:
        """创建使用 pwdlib 当前推荐参数的密码哈希器。"""
        self._password_hash = PasswordHash.recommended()

    async def hash(self, plain_password: str) -> str:
        """在线程中计算密码哈希，避免 CPU 密集操作阻塞事件循环。"""
        return await asyncio.to_thread(self._password_hash.hash, plain_password)

    async def verify(self, plain_password: str, password_hash: str) -> bool:
        """在线程中校验明文密码是否匹配已保存的哈希。"""
        return await asyncio.to_thread(
            self._password_hash.verify,
            plain_password,
            password_hash,
        )
