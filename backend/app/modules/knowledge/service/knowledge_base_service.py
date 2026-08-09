from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.infrastructure.embedding.schemas import EmbeddingProfile
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.modules.knowledge.schema.knowledge_base_schema import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)


class KnowledgeBaseService:
    """编排知识库所有权、名称规则和数据库事务。"""

    def __init__(
        self,
        db: AsyncSession,
        embedding_profile: EmbeddingProfile | None = None,
    ) -> None:
        """组装数据库依赖，并保存新建知识库应使用的 Embedding 快照。"""
        self.db = db
        self.repository = KnowledgeBaseRepository(db)
        self.embedding_profile = (
            embedding_profile or EmbeddingProviderFactory().default_profile()
        )

    async def create(
        self,
        user_id: UUID,
        request: KnowledgeBaseCreate,
    ) -> KnowledgeBaseResponse:
        """创建用户私有知识库，并冻结第一版 Embedding 配置。"""
        await self._ensure_name_available(user_id=user_id, name=request.name)
        try:
            entity = await self.repository.create(
                user_id=user_id,
                request=request,
                embedding_provider=self.embedding_profile.provider,
                embedding_model=self.embedding_profile.model,
                embedding_dimension=self.embedding_profile.dimension,
            )
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            self._raise_integrity_error(exc)
            raise
        except Exception:
            await self.db.rollback()
            raise

        app_logger.info(
            "已创建知识库 user_id=%s knowledge_base_id=%s name=%s",
            user_id,
            entity.id,
            entity.name,
        )
        return KnowledgeBaseResponse.model_validate(entity)

    async def list_knowledge_bases(
        self,
        user_id: UUID,
        include_archived: bool = False,
    ) -> list[KnowledgeBaseResponse]:
        """返回当前用户知识库列表，并默认隐藏已归档记录。"""
        entities = await self.repository.list_by_user(
            user_id=user_id,
            include_archived=include_archived,
        )
        return [KnowledgeBaseResponse.model_validate(entity) for entity in entities]

    async def get(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> KnowledgeBaseResponse:
        """获取当前用户拥有的指定知识库。"""
        entity = await self._get_owned_knowledge_base(user_id, knowledge_base_id)
        return KnowledgeBaseResponse.model_validate(entity)

    async def update(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        changes: KnowledgeBaseUpdate,
    ) -> KnowledgeBaseResponse:
        """修改知识库可编辑字段，并在改名时保护名称唯一性。"""
        entity = await self._get_owned_knowledge_base(user_id, knowledge_base_id)
        update_fields = changes.model_dump(exclude_unset=True)

        new_name = update_fields.get("name")
        if isinstance(new_name, str) and new_name != entity.name:
            await self._ensure_name_available(
                user_id=user_id,
                name=new_name,
                current_knowledge_base_id=entity.id,
            )

        for field_name, value in update_fields.items():
            setattr(entity, field_name, value)

        try:
            await self.repository.update(entity)
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            self._raise_integrity_error(exc)
            raise
        except Exception:
            await self.db.rollback()
            raise

        app_logger.info(
            "知识库已更新 user_id=%s knowledge_base_id=%s fields=%s",
            user_id,
            knowledge_base_id,
            sorted(update_fields),
        )
        return KnowledgeBaseResponse.model_validate(entity)

    async def archive(self, user_id: UUID, knowledge_base_id: UUID) -> None:
        """软归档知识库，保留未来文档、对象文件和审计恢复能力。"""
        entity = await self._get_owned_knowledge_base(user_id, knowledge_base_id)
        if entity.is_archived:
            # 重复 DELETE 保持幂等，不制造无意义的更新时间变化。
            return

        entity.is_archived = True
        try:
            await self.repository.update(entity)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        app_logger.info(
            "知识库已归档 user_id=%s knowledge_base_id=%s",
            user_id,
            knowledge_base_id,
        )

    async def _get_owned_knowledge_base(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> KnowledgeBaseEntity:
        """通过所有权过滤查询，并把不存在与越权统一隐藏为 404。"""
        entity = await self.repository.get_by_id_for_user(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
        )
        if entity is None:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                message="知识库不存在",
                status_code=404,
            )
        return entity

    async def _ensure_name_available(
        self,
        user_id: UUID,
        name: str,
        current_knowledge_base_id: UUID | None = None,
    ) -> None:
        """提供友好的同名预检查，数据库唯一约束仍负责最终并发保护。"""
        existing = await self.repository.get_by_name_for_user(
            user_id=user_id,
            name=name,
        )
        if existing is not None and existing.id != current_knowledge_base_id:
            raise self._name_conflict_exception()

    @staticmethod
    def _raise_integrity_error(exc: IntegrityError) -> None:
        """只把知识库名称唯一约束转换成 409，其他数据库错误继续上抛。"""
        diagnostic = getattr(exc.orig, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name == "uq_knowledge_bases_user_name":
            raise KnowledgeBaseService._name_conflict_exception() from exc

    @staticmethod
    def _name_conflict_exception() -> BusinessException:
        """构造前端可稳定识别的知识库名称冲突异常。"""
        return BusinessException(
            code=ErrorCode.KNOWLEDGE_BASE_NAME_CONFLICT,
            message="知识库名称已存在",
            status_code=409,
        )
