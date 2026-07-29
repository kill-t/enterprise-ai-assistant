from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.entity.user import UserEntity


class UserRepository:
    """封装用户实体的数据库读写，不承担密码处理或事务提交。"""

    def __init__(self, db: AsyncSession):
        """保存当前请求使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        email: str,
        password_hash: str,
    ) -> UserEntity:
        """创建用户并刷新数据库字段，事务由上层 Service 决定是否提交。"""
        entity = UserEntity(
            email=email,
            password_hash=password_hash,
        )
        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def get_by_email(self, email: str) -> UserEntity | None:
        """按标准化邮箱查询用户，供注册去重和登录使用。"""
        stmt = select(UserEntity).where(UserEntity.email == email)
        return await self.db.scalar(stmt)

    async def get_by_id(self, user_id: UUID) -> UserEntity | None:
        """按主键查询用户，供解析当前登录身份使用。"""
        stmt = select(UserEntity).where(UserEntity.id == user_id)
        return await self.db.scalar(stmt)
