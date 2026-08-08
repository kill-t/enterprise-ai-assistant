import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.common.enums import MessageStatus
from app.modules.conversation.repository.message_repository import MessageRepository


def test_mark_cancelled_uses_atomic_pending_status_condition() -> None:
    """取消更新必须在同一条 SQL 中检查 PENDING，避免覆盖并发终态。"""
    database = SimpleNamespace(scalar=AsyncMock(return_value=uuid4()))
    repository = MessageRepository(database)
    message_id = uuid4()
    finished_at = datetime.now(timezone.utc)

    async def scenario() -> bool:
        """执行一次取消更新并返回仓储报告的更新结果。"""
        return await repository.mark_cancelled_if_pending(
            message_id,
            partial_content="部分回答",
            error_code="GENERATION_CANCELLED",
            error_message="回答生成已取消",
            finished_at=finished_at,
        )

    updated = asyncio.run(scenario())
    statement = database.scalar.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert updated is True
    assert "UPDATE messages SET" in sql
    assert "messages.id =" in sql
    assert "messages.status =" in sql
    assert "RETURNING messages.id" in sql
    assert MessageStatus.PENDING in compiled.params.values()
    assert MessageStatus.CANCELLED in compiled.params.values()


def test_mark_cancelled_reports_false_when_message_is_already_terminal() -> None:
    """条件更新没有命中时返回 False，由 Service 保留既有终态。"""
    database = SimpleNamespace(scalar=AsyncMock(return_value=None))
    repository = MessageRepository(database)

    async def scenario() -> bool:
        """模拟数据库发现消息已经不是 PENDING。"""
        return await repository.mark_cancelled_if_pending(
            uuid4(),
            partial_content="部分回答",
            error_code="GENERATION_CANCELLED",
            error_message="回答生成已取消",
            finished_at=datetime.now(timezone.utc),
        )

    assert asyncio.run(scenario()) is False
