"""使用独立数据库事务持久化 LLM 调用终态。"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import SessionLocal
from app.modules.llm_audit.entity.llm_run import LLMRunEntity
from app.modules.llm_audit.models import LLMRunRecord
from app.modules.llm_audit.repository.llm_run_repository import LLMRunRepository


class DatabaseLLMRunRecorder:
    """每次记录都使用独立 Session，避免改变业务事务的提交或回滚。"""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] = SessionLocal,
    ) -> None:
        """允许测试注入内存 Session 工厂，线上默认使用应用 SessionLocal。"""
        self.session_factory = session_factory

    async def record(self, record: LLMRunRecord) -> None:
        """把安全数据映射为 ORM 实体，并在独立事务中提交。"""
        async with self.session_factory() as db:
            try:
                await LLMRunRepository(db).create(
                    LLMRunEntity(
                        request_id=record.request_id,
                        user_id=record.user_id,
                        conversation_id=record.conversation_id,
                        message_id=record.message_id,
                        operation=record.operation,
                        provider=record.provider,
                        model=record.model,
                        status=record.status,
                        prompt_tokens=record.prompt_tokens,
                        completion_tokens=record.completion_tokens,
                        total_tokens=record.total_tokens,
                        latency_ms=record.latency_ms,
                        finish_reason=record.finish_reason,
                        error_code=record.error_code,
                        error_type=record.error_type,
                        started_at=record.started_at,
                        finished_at=record.finished_at,
                    )
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
