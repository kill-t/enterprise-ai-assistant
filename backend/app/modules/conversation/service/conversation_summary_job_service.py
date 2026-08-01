from collections.abc import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import MessageRole, SummaryJobStatus
from app.core.loggers import app_logger
from app.infrastructure.llm.context_manager import MessageTokenCounter
from app.infrastructure.llm.schemas import ChatMessage, ChatRole
from app.infrastructure.llm.token_counter import TokenCounter
from app.infrastructure.task_queue.summary_job_dispatcher import SummaryJobDispatcher
from app.modules.conversation.entity.conversation import ConversationEntity
from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)
from app.modules.conversation.entity.message import MessageEntity
from app.modules.conversation.repository.conversation_summary_job_repository import (
    ConversationSummaryJobRepository,
)
from app.modules.conversation.repository.conversation_summary_repository import (
    ConversationSummaryRepository,
)
from app.modules.conversation.repository.message_repository import MessageRepository


class ConversationSummaryJobService:
    """判断摘要阈值，持久化任务，并在提交后尽力投递到 Celery。"""

    def __init__(
        self,
        db: AsyncSession,
        message_repository: MessageRepository,
        summary_repository: ConversationSummaryRepository,
        job_repository: ConversationSummaryJobRepository,
        dispatcher: SummaryJobDispatcher,
        summary_trigger_tokens: int,
        recent_history_budget_tokens: int = 2000,
        summary_batch_tokens: int = 4000,
        token_counter_factory: Callable[[str], MessageTokenCounter] = TokenCounter,
    ) -> None:
        """组装任务判断所需的数据库、投递器、阈值和 token 计数器。"""
        self.db = db
        self.message_repository = message_repository
        self.summary_repository = summary_repository
        self.job_repository = job_repository
        self.dispatcher = dispatcher
        self.summary_trigger_tokens = summary_trigger_tokens
        self.recent_history_budget_tokens = recent_history_budget_tokens
        self.summary_batch_tokens = summary_batch_tokens
        self.token_counter_factory = token_counter_factory

    async def maybe_schedule(
        self,
        conversation: ConversationEntity,
    ) -> ConversationSummaryJobEntity | None:
        """超过阈值时创建任务；已有活动任务时只处理必要的重新投递。"""
        active_job = await self.job_repository.get_active_by_conversation_id(
            conversation.id
        )
        if active_job is not None:
            await self._dispatch_if_needed(active_job)
            return active_job

        messages = await self._get_uncovered_messages(conversation.id)
        messages = self._through_latest_assistant(messages)
        if not messages:
            return None

        counter = self.token_counter_factory(conversation.model_name)
        token_count = counter.count_messages(
            [
                ChatMessage(
                    role=ChatRole(message.role.value),
                    content=message.content,
                )
                for message in messages
            ]
        )
        if token_count <= self.summary_trigger_tokens:
            return None

        messages_to_summarize = self._select_summary_prefix(messages, counter)
        if not messages_to_summarize:
            return None

        target_message = messages_to_summarize[-1]
        job = ConversationSummaryJobEntity(
            id=uuid4(),
            conversation_id=conversation.id,
            target_message_id=target_message.id,
            target_created_at=target_message.created_at,
            idempotency_key=(
                f"conversation-summary:{conversation.id}:{target_message.id}"
            ),
            status=SummaryJobStatus.PENDING,
            attempts=0,
            max_attempts=3,
        )
        await self.job_repository.create(job)
        await self._commit()
        app_logger.info(
            "已创建会话摘要任务 conversation_id=%s job_id=%s target_message_id=%s",
            conversation.id,
            job.id,
            target_message.id,
        )
        await self._dispatch_if_needed(job)
        return job

    async def _get_uncovered_messages(
        self,
        conversation_id: UUID,
    ) -> list[MessageEntity]:
        """读取尚未被摘要覆盖的全部已完成消息。"""
        summary = await self.summary_repository.get_by_conversation_id(conversation_id)
        if summary is None:
            return await self.message_repository.list_completed_by_conversation(
                conversation_id
            )
        return await self.message_repository.list_completed_after_cursor(
            conversation_id=conversation_id,
            covered_until_created_at=summary.covered_until_created_at,
            covered_until_message_id=summary.covered_until_message_id,
        )

    @staticmethod
    def _through_latest_assistant(
        messages: list[MessageEntity],
    ) -> list[MessageEntity]:
        """只保留到最近助手回答为止的消息，避免冻结半个问答轮次。"""
        latest_assistant_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].role == MessageRole.ASSISTANT
            ),
            None,
        )
        if latest_assistant_index is None:
            return []
        return messages[: latest_assistant_index + 1]

    def _select_summary_prefix(
        self,
        messages: list[MessageEntity],
        counter: MessageTokenCounter,
    ) -> list[MessageEntity]:
        """选择需要压缩的旧完整轮次，并为近期历史保留原文预算。"""
        turns = self._group_complete_turns(messages)
        retained_tokens = 0
        retained_start = len(turns)

        # 从最新轮次向前保留原文；第一个超出预算的轮次及其更早历史进入摘要。
        for index in range(len(turns) - 1, -1, -1):
            turn = turns[index]
            turn_tokens = self._count_turn_tokens(turn, counter)
            if retained_tokens + turn_tokens > self.recent_history_budget_tokens:
                break
            retained_tokens += turn_tokens
            retained_start = index

        candidate_turns = turns[:retained_start]
        selected_turns: list[list[MessageEntity]] = []
        selected_tokens = 0

        # 从最旧轮次开始连续推进游标；不能跳过中间历史只摘要更晚内容。
        for turn in candidate_turns:
            turn_tokens = self._count_turn_tokens(turn, counter)
            if (
                selected_turns
                and selected_tokens + turn_tokens > self.summary_batch_tokens
            ):
                break
            # 第一轮即使超过软上限也保持完整，最终硬上限由 Processor 调用前校验。
            selected_turns.append(turn)
            selected_tokens += turn_tokens

        return [message for turn in selected_turns for message in turn]

    @staticmethod
    def _count_turn_tokens(
        turn: list[MessageEntity],
        counter: MessageTokenCounter,
    ) -> int:
        """把一个完整数据库轮次转换为标准消息并估算 token。"""
        return counter.count_messages(
            [
                ChatMessage(
                    role=ChatRole(message.role.value),
                    content=message.content,
                )
                for message in turn
            ]
        )

    @staticmethod
    def _group_complete_turns(
        messages: list[MessageEntity],
    ) -> list[list[MessageEntity]]:
        """把消息整理成以用户开始、以助手结束的完整问答轮次。"""
        turns: list[list[MessageEntity]] = []
        current_turn: list[MessageEntity] = []

        for message in messages:
            if message.role == MessageRole.USER:
                if current_turn and current_turn[-1].role == MessageRole.ASSISTANT:
                    turns.append(current_turn)
                current_turn = [message]
                continue

            if current_turn:
                current_turn.append(message)

        if current_turn and current_turn[-1].role == MessageRole.ASSISTANT:
            turns.append(current_turn)
        return turns

    async def _dispatch_if_needed(
        self,
        job: ConversationSummaryJobEntity,
    ) -> None:
        """只重新投递尚未确认投递成功的 PENDING 任务。"""
        if (
            job.status != SummaryJobStatus.PENDING
            or job.celery_task_id is not None
        ):
            return

        try:
            celery_task_id = await self.dispatcher.dispatch(job.id)
        except Exception as exc:
            # 数据库中的 PENDING 任务就是恢复依据，Broker 故障不删除任务。
            app_logger.warning(
                "摘要任务投递失败，将保留待处理状态 job_id=%s reason=%s",
                job.id,
                type(exc).__name__,
                exc_info=True,
            )
            return

        job.celery_task_id = celery_task_id
        await self.job_repository.update(job)
        await self._commit()
        app_logger.info(
            "摘要任务已投递 job_id=%s celery_task_id=%s",
            job.id,
            celery_task_id,
        )

    async def _commit(self) -> None:
        """提交任务事务，并在失败时回滚当前异步 Session。"""
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise
