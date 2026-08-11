"""提供不连接数据库或搜索服务的可控词法检索实现。"""

from app.modules.knowledge.retrieval.lexical_retriever import (
    BaseLexicalRetriever,
    LexicalRetrievalCandidate,
    LexicalRetrievalError,
    LexicalRetrievalRequest,
    LexicalRetrievalResult,
)


class MockLexicalRetriever(BaseLexicalRetriever):
    """按预设顺序返回候选，或稳定模拟词法检索后端失败。"""

    def __init__(
        self,
        *,
        candidates: tuple[LexicalRetrievalCandidate, ...] = (),
        failure_message: str | None = None,
    ) -> None:
        """保存预设候选和请求记录，便于单元测试核对调用参数。"""
        self.candidates = candidates
        self.failure_message = failure_message
        self.requests: list[LexicalRetrievalRequest] = []

    async def retrieve(
        self,
        request: LexicalRetrievalRequest,
    ) -> LexicalRetrievalResult:
        """不产生外部 I/O，仅执行预设的成功或失败分支。"""
        self.requests.append(request)
        if self.failure_message is not None:
            raise LexicalRetrievalError(self.failure_message)
        return LexicalRetrievalResult(
            candidates=self.candidates[: request.candidate_limit]
        )
