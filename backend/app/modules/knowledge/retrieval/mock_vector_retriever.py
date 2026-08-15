"""提供无需 Embedding API 和数据库的可控向量候选实现。"""

from app.modules.knowledge.retrieval.vector_retriever import (
    BaseVectorRetriever,
    VectorRetrievalCandidate,
    VectorRetrievalError,
    VectorRetrievalRequest,
    VectorRetrievalResult,
    VectorRetrievalScopeError,
)


class MockVectorRetriever(BaseVectorRetriever):
    """按预设顺序返回候选，并稳定模拟通道或权限范围失败。"""

    def __init__(
        self,
        *,
        candidates: tuple[VectorRetrievalCandidate, ...] = (),
        failure_message: str | None = None,
        scope_failure: bool = False,
    ) -> None:
        """保存预设行为和请求记录，供混合编排单元测试断言。"""
        self.candidates = candidates
        self.failure_message = failure_message
        self.scope_failure = scope_failure
        self.requests: list[VectorRetrievalRequest] = []

    async def retrieve(
        self,
        request: VectorRetrievalRequest,
    ) -> VectorRetrievalResult:
        """不产生外部 I/O，仅执行成功、降级或不可降级的预设分支。"""
        self.requests.append(request)
        if self.scope_failure:
            raise VectorRetrievalScopeError("知识库范围无效")
        if self.failure_message is not None:
            raise VectorRetrievalError(self.failure_message)
        result = VectorRetrievalResult(
            candidates=self.candidates[: request.channel_candidate_limit]
        )
        result.ensure_valid_for(request)
        return result
