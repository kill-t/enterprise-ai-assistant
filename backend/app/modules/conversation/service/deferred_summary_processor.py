from app.modules.conversation.entity.conversation_summary_job import (
    ConversationSummaryJobEntity,
)


class SummaryProcessorNotReadyError(RuntimeError):
    """表示真实的会话摘要生成器尚未接入。"""


class DeferredConversationSummaryProcessor:
    """在真实摘要生成器完成前明确失败，避免把空处理误记为成功。"""

    async def process(self, job: ConversationSummaryJobEntity) -> None:
        """拒绝伪造成功结果，下一模块会替换为真正的摘要处理器。"""
        raise SummaryProcessorNotReadyError(
            f"会话摘要处理器尚未实现，无法处理任务 {job.id}"
        )
