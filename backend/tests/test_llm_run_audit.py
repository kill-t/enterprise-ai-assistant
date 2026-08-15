import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.common.enums import LLMRunStatus
from app.infrastructure.llm.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatRole,
    ChatStreamCompletedEvent,
    ChatStreamDeltaEvent,
    ChatStreamErrorEvent,
    ChatStreamEvent,
)
from app.modules.llm_audit.audited_provider import AuditedChatProvider
from app.modules.llm_audit.entity.llm_run import LLMRunEntity
from app.modules.llm_audit.models import LLMRunRecord
from app.modules.llm_audit.recorder import DatabaseLLMRunRecorder
from app.modules.llm_audit.repository.llm_run_repository import LLMRunRepository


class CollectingRecorder:
    """在内存中收集审计记录，避免单元测试连接真实 PostgreSQL。"""

    def __init__(self, error: Exception | None = None) -> None:
        """允许测试主动模拟审计数据库故障。"""
        self.records: list[LLMRunRecord] = []
        self.error = error

    async def record(self, record: LLMRunRecord) -> None:
        """保存一条记录，或按测试配置抛出故障。"""
        if self.error is not None:
            raise self.error
        self.records.append(record)


class SessionContext:
    """把测试 Session 包装成 DatabaseLLMRunRecorder 需要的异步上下文。"""

    def __init__(self, session) -> None:
        """保存进入上下文时返回的 Session 替身。"""
        self.session = session

    async def __aenter__(self):
        """返回本次独立审计事务的 Session。"""
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """测试替身不额外处理异常。"""
        return None


class SuccessfulProvider:
    """返回固定用量的普通调用 Provider。"""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """返回不包含输入正文的固定结果。"""
        return ChatResponse(
            content="内部敏感回答",
            model=request.model or "actual-model",
            prompt_tokens=12,
            completion_tokens=3,
            total_tokens=15,
            finish_reason="stop",
        )


class FailingProvider:
    """模拟厂商非流式调用失败。"""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """异常正文包含敏感值，用来验证数据库不会保存它。"""
        raise TimeoutError("不得写入数据库的内部地址和密钥")


class StreamingProvider:
    """根据测试模式产生成功、失败或可中断的内部流。"""

    def __init__(self, mode: str) -> None:
        """保存当前流式测试模式。"""
        self.mode = mode

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """该测试只使用原生流，不应进入普通调用。"""
        raise AssertionError("不应调用 chat")

    async def stream_chat(
        self,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        """先产生一个增量，再根据模式产生终态或等待关闭。"""
        yield ChatStreamDeltaEvent(delta="会议")
        if self.mode == "success":
            yield ChatStreamCompletedEvent(
                response=ChatResponse(
                    content="会议",
                    model=request.model or "stream-model",
                    prompt_tokens=20,
                    completion_tokens=2,
                    total_tokens=22,
                )
            )
            return
        if self.mode == "error":
            yield ChatStreamErrorEvent(
                error_code="LLM_STREAM_ERROR",
                error_message="模型流式调用失败",
                model=request.model or "stream-model",
            )
            return

        await asyncio.Event().wait()


def build_request() -> ChatRequest:
    """创建带非敏感关联 ID 的审计请求。"""
    return ChatRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="不得进入审计表的问题")],
        model="requested-model",
        metadata={
            "operation": "conversation_chat",
            "user_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "message_id": str(uuid4()),
            "source_count": "2",
        },
    )


def build_provider(
    provider,
    recorder: CollectingRecorder,
) -> AuditedChatProvider:
    """使用可预测时间组装待测试的审计装饰器。"""
    started_at = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
    times = iter([10.0, 10.125])
    datetimes = iter([started_at, started_at + timedelta(milliseconds=125)])
    return AuditedChatProvider(
        provider=provider,
        recorder=recorder,
        provider_name="test-provider",
        default_model="default-model",
        clock=lambda: next(times),
        now_factory=lambda: next(datetimes),
    )


def test_llm_run_table_keeps_metadata_without_sensitive_content_columns() -> None:
    """审计表应能关联调用，但不得设计 Prompt、正文或回答字段。"""
    table = LLMRunEntity.__table__
    column_names = set(table.c.keys())

    assert {
        "id",
        "request_id",
        "user_id",
        "conversation_id",
        "message_id",
        "operation",
        "provider",
        "model",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
        "finish_reason",
        "error_code",
        "error_type",
        "started_at",
        "finished_at",
        "created_at",
    } == column_names
    assert not {"prompt", "request", "response", "content", "error_message"} & column_names
    assert any(
        constraint.name == "ck_llm_runs_tokens_nonnegative"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "ck_llm_runs_latency_nonnegative"
        for constraint in table.constraints
    )
    # Recorder 也会在 Celery 或独立脚本中运行，外键不能依赖 FastAPI 恰好先导入其他实体。
    assert {foreign_key.column.table.name for foreign_key in table.foreign_keys} == {
        "users",
        "conversations",
        "messages",
    }


def test_repository_adds_and_flushes_without_committing() -> None:
    """Repository 只负责写入，独立事务提交由 Recorder 控制。"""
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    repository = LLMRunRepository(db)
    entity = LLMRunEntity(
        operation="conversation_chat",
        provider="mock",
        model="mock",
        status=LLMRunStatus.SUCCEEDED,
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        latency_ms=10.0,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )

    result = asyncio.run(repository.create(entity))

    assert result is entity
    db.add.assert_called_once_with(entity)
    db.flush.assert_awaited_once()
    assert not hasattr(db, "commit")


def test_database_recorder_uses_its_own_session_and_commits_terminal_record() -> None:
    """Recorder 应独立打开并提交审计事务，不借用聊天业务 Session。"""
    session = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    session_factory = Mock(return_value=SessionContext(session))
    now = datetime.now(timezone.utc)
    record = LLMRunRecord(
        request_id=None,
        user_id=None,
        conversation_id=None,
        message_id=None,
        operation="conversation_summary",
        provider="mock",
        model="mock",
        status=LLMRunStatus.SUCCEEDED,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=20.0,
        finish_reason="stop",
        error_code=None,
        error_type=None,
        started_at=now,
        finished_at=now,
    )

    asyncio.run(DatabaseLLMRunRecorder(session_factory).record(record))

    session_factory.assert_called_once_with()
    entity = session.add.call_args.args[0]
    assert isinstance(entity, LLMRunEntity)
    assert entity.operation == "conversation_summary"
    assert entity.total_tokens == 15
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


def test_non_stream_success_records_usage_and_safe_correlations() -> None:
    """普通成功调用应记录真实模型、用量、耗时和允许的关联字段。"""
    recorder = CollectingRecorder()
    provider = build_provider(SuccessfulProvider(), recorder)
    request = build_request()

    response = asyncio.run(provider.chat(request))
    record = recorder.records[0]

    assert response.content == "内部敏感回答"
    assert record.status == LLMRunStatus.SUCCEEDED
    assert record.operation == "conversation_chat"
    assert record.provider == "test-provider"
    assert record.model == "requested-model"
    assert record.prompt_tokens == 12
    assert record.completion_tokens == 3
    assert record.total_tokens == 15
    assert record.latency_ms == 125.0
    assert record.user_id.hex == request.metadata["user_id"].replace("-", "")
    assert record.conversation_id.hex == request.metadata["conversation_id"].replace("-", "")
    assert record.message_id.hex == request.metadata["message_id"].replace("-", "")
    assert "内部敏感回答" not in repr(record)
    assert "不得进入审计表的问题" not in repr(record)


def test_non_stream_failure_records_only_safe_error_type_and_reraises() -> None:
    """厂商异常应形成 FAILED 记录，但异常正文仍只留在受控日志。"""
    recorder = CollectingRecorder()
    provider = build_provider(FailingProvider(), recorder)

    with pytest.raises(TimeoutError, match="内部地址"):
        asyncio.run(provider.chat(build_request()))

    record = recorder.records[0]
    assert record.status == LLMRunStatus.FAILED
    assert record.error_code == "LLM_REQUEST_FAILED"
    assert record.error_type == "TimeoutError"
    assert record.prompt_tokens == 0
    assert "内部地址" not in repr(record)


def test_audit_write_failure_does_not_break_successful_model_call() -> None:
    """审计数据库故障属于可观测性降级，不得覆盖已经成功的模型结果。"""
    recorder = CollectingRecorder(error=RuntimeError("audit database unavailable"))
    provider = build_provider(SuccessfulProvider(), recorder)

    response = asyncio.run(provider.chat(build_request()))

    assert response.total_tokens == 15


def test_stream_success_and_error_each_create_one_terminal_record() -> None:
    """流式成功或安全错误事件都应只形成一条终态审计记录。"""

    async def scenario(mode: str) -> tuple[list[ChatStreamEvent], list[LLMRunRecord]]:
        """消费完整流并返回事件与审计记录。"""
        recorder = CollectingRecorder()
        provider = build_provider(StreamingProvider(mode), recorder)
        events = [event async for event in provider.stream_chat(build_request())]
        return events, recorder.records

    success_events, success_records = asyncio.run(scenario("success"))
    error_events, error_records = asyncio.run(scenario("error"))

    assert isinstance(success_events[-1], ChatStreamCompletedEvent)
    assert len(success_records) == 1
    assert success_records[0].status == LLMRunStatus.SUCCEEDED
    assert success_records[0].total_tokens == 22
    assert isinstance(error_events[-1], ChatStreamErrorEvent)
    assert len(error_records) == 1
    assert error_records[0].status == LLMRunStatus.FAILED
    assert error_records[0].error_code == "LLM_STREAM_ERROR"


def test_closing_stream_after_first_delta_records_cancelled() -> None:
    """客户端中断消费后应记录 CANCELLED，而不是伪装成模型失败。"""

    async def scenario() -> tuple[ChatStreamEvent, list[LLMRunRecord]]:
        """读取首个增量后主动关闭外层审计流。"""
        recorder = CollectingRecorder()
        provider = build_provider(StreamingProvider("wait"), recorder)
        stream = provider.stream_chat(build_request())
        first_event = await anext(stream)
        await stream.aclose()
        return first_event, recorder.records

    first_event, records = asyncio.run(scenario())

    assert isinstance(first_event, ChatStreamDeltaEvent)
    assert len(records) == 1
    assert records[0].status == LLMRunStatus.CANCELLED
    assert records[0].error_code == "LLM_STREAM_CANCELLED"


def test_llm_run_migration_follows_current_head_and_can_downgrade() -> None:
    """审计表迁移必须接在当前 RAG 迁移之后，并完整删除索引、表和枚举。"""
    migration = Path(
        "migrations/versions/a1c5e9d3f702_create_llm_runs_table.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "f4c8a2d6e190"' in migration
    assert '"llm_runs"' in migration
    assert '"llm_run_status"' in migration
    assert 'op.drop_table("llm_runs")' in migration
    assert 'sa.Enum(name="llm_run_status").drop' in migration
