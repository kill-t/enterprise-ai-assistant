"""把 pg_trgm Repository 适配为统一词法检索接口。"""

from sqlalchemy.exc import SQLAlchemyError

from app.core.loggers import app_logger
from app.modules.knowledge.repository.pg_trgm_lexical_repository import (
    PgTrgmLexicalRepository,
)
from app.modules.knowledge.retrieval.lexical_retriever import (
    BaseLexicalRetriever,
    LexicalRetrievalError,
    LexicalRetrievalRequest,
    LexicalRetrievalResult,
)


class PgTrgmLexicalRetriever(BaseLexicalRetriever):
    """使用字符 trigram 补充向量召回中的精确名词、编号和日期。"""

    def __init__(
        self,
        *,
        repository: PgTrgmLexicalRepository,
        minimum_similarity: float = 0.1,
    ) -> None:
        """保存只读 Repository，并校验 pg_trgm 渠道的最低分数。"""
        if not 0.0 <= minimum_similarity <= 1.0:
            raise ValueError("pg_trgm minimum_similarity 必须在 0 到 1 之间")
        self.repository = repository
        self.minimum_similarity = minimum_similarity

    async def retrieve(
        self,
        request: LexicalRetrievalRequest,
    ) -> LexicalRetrievalResult:
        """执行带权限范围的查询，并把基础设施故障包装成安全通道错误。"""
        try:
            candidates = await self.repository.search_candidates(
                user_id=request.user_id,
                knowledge_base_ids=request.knowledge_base_ids,
                query=request.query,
                candidate_limit=request.candidate_limit,
                minimum_similarity=self.minimum_similarity,
            )
            result = LexicalRetrievalResult(candidates=tuple(candidates))
            result.ensure_valid_for(request)
            return result
        except LexicalRetrievalError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            app_logger.warning(
                "pg_trgm 词法检索失败 knowledge_base_count=%s",
                len(request.knowledge_base_ids),
            )
            raise LexicalRetrievalError("关键词检索服务暂时不可用") from exc
