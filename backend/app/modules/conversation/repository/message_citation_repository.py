"""封装助手消息引用快照的整组替换写入。"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.entity.message_citation import MessageCitationEntity
from app.modules.knowledge.schema.knowledge_search_schema import (
    MultiKnowledgeSearchHit,
)
from app.modules.knowledge.source_locator import serialize_source_locator


class MessageCitationRepository:
    """把最终发送给模型的来源保存为可追溯活引用和不可变快照。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存当前业务操作共用的异步数据库 Session。"""
        self.db = db

    async def list_by_message_ids(
        self,
        message_ids: tuple[UUID, ...],
    ) -> list[MessageCitationEntity]:
        """一次读取多条消息的引用，避免历史列表出现 N+1 查询。"""
        if not message_ids:
            return []

        statement = (
            select(MessageCitationEntity)
            .where(MessageCitationEntity.message_id.in_(message_ids))
            .order_by(
                MessageCitationEntity.message_id,
                MessageCitationEntity.source_order,
            )
        )
        return list((await self.db.scalars(statement)).all())

    async def replace_for_message(
        self,
        message_id: UUID,
        sources: tuple[MultiKnowledgeSearchHit, ...],
    ) -> list[MessageCitationEntity]:
        """删除旧引用并按 S1 开始的顺序暂存本次完整来源，不自行提交。"""
        await self.db.execute(
            delete(MessageCitationEntity).where(
                MessageCitationEntity.message_id == message_id
            )
        )
        entities = [
            self._to_entity(
                message_id=message_id,
                source=source,
                source_order=source_order,
            )
            for source_order, source in enumerate(sources, start=1)
        ]
        if entities:
            self.db.add_all(entities)
        await self.db.flush()
        return entities

    @staticmethod
    def _to_entity(
        message_id: UUID,
        source: MultiKnowledgeSearchHit,
        source_order: int,
    ) -> MessageCitationEntity:
        """同时映射当前外键和回答生成当时不可随来源变化的展示快照。"""
        return MessageCitationEntity(
            message_id=message_id,
            knowledge_base_id=source.knowledge_base_id,
            document_id=source.document_id,
            chunk_id=source.chunk_id,
            knowledge_base_name_snapshot=source.knowledge_base_name,
            filename_snapshot=source.filename,
            chunk_index_snapshot=source.chunk_index,
            content_snapshot=source.content,
            heading_path_snapshot=source.heading_path,
            page_start=source.page_start,
            page_end=source.page_end,
            source_type_snapshot=source.source_type,
            source_locator_snapshot=serialize_source_locator(
                source.source_locator
            ),
            source_order=source_order,
            retrieval_score=source.score,
        )
