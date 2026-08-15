"""集中组装知识搜索 Service，保证搜索 API 与会话 RAG 使用相同策略。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RagSettings, settings
from app.infrastructure.embedding.provider_factory import EmbeddingProviderFactory
from app.modules.knowledge.retrieval.evidence_selection import BaseEvidenceSelector
from app.modules.knowledge.retrieval.evidence_selector_factory import (
    create_configured_evidence_selector,
)
from app.modules.knowledge.service.knowledge_search_service import (
    KnowledgeSearchService,
)


def create_knowledge_search_service(
    *,
    db: AsyncSession,
    rag_settings: RagSettings | None = None,
    evidence_selector: BaseEvidenceSelector | None = None,
) -> KnowledgeSearchService:
    """按当前 RAG 配置组装候选召回和唯一的第二阶段证据选择实现。"""
    resolved_rag_settings = rag_settings or settings.rag
    resolved_selector = evidence_selector or create_configured_evidence_selector(
        rag_settings=resolved_rag_settings,
    )
    return KnowledgeSearchService(
        db=db,
        embedding_factory=EmbeddingProviderFactory(),
        # LLM 自己负责证据充分性，-1 表示候选进入第二阶段前不应用旧向量阈值。
        similarity_threshold=(
            -1.0 if resolved_rag_settings.evidence_selector == "llm" else None
        ),
        candidate_multiplier=resolved_rag_settings.search_candidate_multiplier,
        evidence_selector=resolved_selector,
    )
