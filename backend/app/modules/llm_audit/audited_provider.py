"""用装饰器统一审计普通与流式 LLM Provider 调用。"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import datetime, timezone
from uuid import UUID

from app.common.context.request_context import get_request_id
from app.common.enums import LLMRunStatus
from app.core.loggers import app_logger
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamCompletedEvent,
    ChatStreamErrorEvent,
    ChatStreamEvent,
)
from app.modules.llm_audit.models import LLMRunRecord, LLMRunRecorder


_SAFE_OPERATION_PATTERN = re.compile(r"^[a-z0-9_.:-]{1,100}$")


class AuditedChatProvider(BaseChatProvider):
    """代理真实 Provider，并尽力保存每次调用的非敏感终态。"""

    def __init__(
        self,
        *,
        provider: BaseChatProvider,
        recorder: LLMRunRecorder,
        provider_name: str,
        default_model: str,
        clock: Callable[[], float] = time.perf_counter,
        now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """注入被代理 Provider、持久化端和可测试时钟。"""
        normalized_provider = provider_name.strip().lower()
        normalized_model = default_model.strip()
        if not normalized_provider:
            raise ValueError("审计 Provider 名称不能为空")
        if not normalized_model:
            raise ValueError("审计默认模型名称不能为空")
        self.provider = provider
        self.recorder = recorder
        self.provider_name = normalized_provider[:50]
        self.default_model = normalized_model[:255]
        self.clock = clock
        self.now_factory = now_factory

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """记录普通调用的成功、异常或任务取消终态。"""
        started_at = self.now_factory()
        started_clock = self.clock()
        try:
            response = await self.provider.chat(request)
        except asyncio.CancelledError:
            await self._record_terminal(
                request=request,
                status=LLMRunStatus.CANCELLED,
                model=self._requested_model(request),
                started_at=started_at,
                started_clock=started_clock,
                error_code="LLM_REQUEST_CANCELLED",
                error_type="CancelledError",
            )
            raise
        except Exception as exc:
            await self._record_terminal(
                request=request,
                status=LLMRunStatus.FAILED,
                model=self._requested_model(request),
                started_at=started_at,
                started_clock=started_clock,
                error_code="LLM_REQUEST_FAILED",
                error_type=type(exc).__name__,
            )
            raise

        await self._record_terminal(
            request=request,
            status=LLMRunStatus.SUCCEEDED,
            model=response.model,
            started_at=started_at,
            started_clock=started_clock,
            response=response,
        )
        return response

    async def stream_chat(
        self,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        """转发增量，并在成功、错误、协议缺失或消费中断时记录一次终态。"""
        started_at = self.now_factory()
        started_clock = self.clock()
        terminal_recorded = False
        try:
            # 外层关闭或取消时同时关闭真实 Provider 流，及时释放 HTTP 连接。
            async with aclosing(self.provider.stream_chat(request)) as stream:
                async for event in stream:
                    if isinstance(event, ChatStreamCompletedEvent):
                        await self._record_terminal(
                            request=request,
                            status=LLMRunStatus.SUCCEEDED,
                            model=event.response.model,
                            started_at=started_at,
                            started_clock=started_clock,
                            response=event.response,
                        )
                        terminal_recorded = True
                        yield event
                        return
                    elif isinstance(event, ChatStreamErrorEvent):
                        await self._record_terminal(
                            request=request,
                            status=LLMRunStatus.FAILED,
                            model=event.model or self._requested_model(request),
                            started_at=started_at,
                            started_clock=started_clock,
                            error_code=event.error_code,
                            error_type="ProviderStreamError",
                        )
                        terminal_recorded = True
                        yield event
                        return
                    yield event

            if not terminal_recorded:
                await self._record_terminal(
                    request=request,
                    status=LLMRunStatus.FAILED,
                    model=self._requested_model(request),
                    started_at=started_at,
                    started_clock=started_clock,
                    error_code="LLM_STREAM_PROTOCOL_ERROR",
                    error_type="MissingTerminalEvent",
                )
                terminal_recorded = True
        except (asyncio.CancelledError, GeneratorExit):
            if not terminal_recorded:
                await self._record_terminal(
                    request=request,
                    status=LLMRunStatus.CANCELLED,
                    model=self._requested_model(request),
                    started_at=started_at,
                    started_clock=started_clock,
                    error_code="LLM_STREAM_CANCELLED",
                    error_type="CancelledError",
                )
            raise
        except Exception as exc:
            if not terminal_recorded:
                await self._record_terminal(
                    request=request,
                    status=LLMRunStatus.FAILED,
                    model=self._requested_model(request),
                    started_at=started_at,
                    started_clock=started_clock,
                    error_code="LLM_STREAM_FAILED",
                    error_type=type(exc).__name__,
                )
            raise

    async def _record_terminal(
        self,
        *,
        request: ChatRequest,
        status: LLMRunStatus,
        model: str,
        started_at: datetime,
        started_clock: float,
        response: ChatResponse | None = None,
        error_code: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """构造不含正文的记录；审计故障只降级记录日志。"""
        finished_at = self.now_factory()
        record = LLMRunRecord(
            request_id=(get_request_id() or None),
            user_id=self._metadata_uuid(request, "user_id"),
            conversation_id=self._metadata_uuid(request, "conversation_id"),
            message_id=self._metadata_uuid(request, "message_id"),
            operation=self._safe_operation(request),
            provider=self.provider_name,
            model=(model.strip() or self.default_model)[:255],
            status=status,
            prompt_tokens=response.prompt_tokens if response is not None else 0,
            completion_tokens=(
                response.completion_tokens if response is not None else 0
            ),
            total_tokens=response.total_tokens if response is not None else 0,
            latency_ms=max(0.0, (self.clock() - started_clock) * 1000),
            finish_reason=response.finish_reason if response is not None else None,
            error_code=error_code[:100] if error_code is not None else None,
            error_type=error_type[:100] if error_type is not None else None,
            started_at=started_at,
            finished_at=finished_at,
        )
        try:
            await self.recorder.record(record)
        except Exception as exc:
            # 可观测性是可降级能力，不能覆盖已经成功的模型结果或原始模型异常。
            app_logger.warning(
                "LLM 调用审计写入失败 provider=%s model=%s operation=%s reason=%s",
                record.provider,
                record.model,
                record.operation,
                type(exc).__name__,
            )

    def _requested_model(self, request: ChatRequest) -> str:
        """在厂商尚未返回实际模型时使用请求模型或工厂默认值。"""
        return request.model or self.default_model

    @staticmethod
    def _metadata_uuid(request: ChatRequest, key: str) -> UUID | None:
        """只解析三个允许的 UUID 关联键，非法值不进入数据库。"""
        value = request.metadata.get(key)
        if value is None:
            return None
        try:
            return UUID(value)
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _safe_operation(request: ChatRequest) -> str:
        """只接受短小机器标识，避免把自由文本误写入 operation。"""
        operation = request.metadata.get("operation", "unspecified").strip().lower()
        if not _SAFE_OPERATION_PATTERN.fullmatch(operation):
            return "unspecified"
        return operation
