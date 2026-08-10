from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Repository 通过聚合入口导入实体，保证字符串 relationship 引用的全部模型已注册。
from app.modules.knowledge.models import KnowledgeBaseEntity
from app.modules.knowledge.schema.knowledge_base_schema import KnowledgeBaseCreate


class KnowledgeBaseRepository:
    """封装知识库 SQL 查询，但不决定业务事务是否提交。"""

    def __init__(self, db: AsyncSession):
        """保存当前 HTTP 请求使用的异步数据库 Session。"""
        self.db = db

    async def create(
        self,
        user_id: UUID,
        request: KnowledgeBaseCreate,
        embedding_provider: str,
        embedding_model: str,
        embedding_dimension: int,
    ) -> KnowledgeBaseEntity:
        """创建知识库实体并刷新数据库生成的 ID 和时间字段。"""
        entity = KnowledgeBaseEntity(
            user_id=user_id,
            name=request.name,
            description=request.description,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
        )
        self.db.add(entity)
        await self.db.flush()
        await self.db.refresh(entity)
        return entity

    async def get_by_id_for_user(
        self,
        knowledge_base_id: UUID,
        user_id: UUID,
    ) -> KnowledgeBaseEntity | None:
        """同时按主键和所有者查询，避免只凭 UUID 越权访问。"""
        statement = select(KnowledgeBaseEntity).where(
            KnowledgeBaseEntity.id == knowledge_base_id,
            KnowledgeBaseEntity.user_id == user_id,
        )
        return await self.db.scalar(statement)

    async def get_by_name_for_user(
        self,
        user_id: UUID,
        name: str,
    ) -> KnowledgeBaseEntity | None:
        """检查同一用户是否已有完全同名的知识库。"""
        statement = select(KnowledgeBaseEntity).where(
            KnowledgeBaseEntity.user_id == user_id,
            KnowledgeBaseEntity.name == name,
        )
        return await self.db.scalar(statement)

    async def list_by_ids_for_user(
        self,
        user_id: UUID,
        knowledge_base_ids: list[UUID],
        for_update: bool = False,
    ) -> list[KnowledgeBaseEntity]:
        """批量读取当前用户的知识库，并可锁定记录保护后续绑定事务。"""
        if not knowledge_base_ids:
            return []
        statement = select(KnowledgeBaseEntity).where(
            KnowledgeBaseEntity.user_id == user_id,
            KnowledgeBaseEntity.id.in_(knowledge_base_ids),
        )
        if for_update:
            # 锁定后，并发归档必须等待本次绑定事务结束，避免校验和写入之间状态变化。
            statement = statement.with_for_update()
        return list((await self.db.scalars(statement)).all())

    async def list_by_user(
        self,
        user_id: UUID,
        include_archived: bool = False,
    ) -> list[KnowledgeBaseEntity]:
        """按更新时间倒序返回当前用户的知识库，并默认隐藏归档项。"""
        statement = select(KnowledgeBaseEntity).where(
            KnowledgeBaseEntity.user_id == user_id
        )
        if not include_archived:
            statement = statement.where(KnowledgeBaseEntity.is_archived.is_(False))
        statement = statement.order_by(KnowledgeBaseEntity.updated_at.desc())
        return list((await self.db.scalars(statement)).all())

    async def update(
        self,
        knowledge_base: KnowledgeBaseEntity,
    ) -> KnowledgeBaseEntity:
        """刷新当前事务内已修改的实体，不在 Repository 中提交。"""
        await self.db.flush()
        await self.db.refresh(knowledge_base)
        return knowledge_base
