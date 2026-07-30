from app.common.enums import SummaryJobStatus


class SummaryJobStateMachine:
    """集中约束摘要任务允许的生命周期转换。"""

    ALLOWED_TRANSITIONS = {
        SummaryJobStatus.PENDING: {SummaryJobStatus.RUNNING},
        SummaryJobStatus.RUNNING: {
            SummaryJobStatus.SUCCEEDED,
            SummaryJobStatus.FAILED,
        },
        SummaryJobStatus.FAILED: {SummaryJobStatus.PENDING},
        SummaryJobStatus.SUCCEEDED: set(),
    }

    @classmethod
    def ensure_transition(
        cls,
        current: SummaryJobStatus,
        target: SummaryJobStatus,
    ) -> None:
        """拒绝跳过执行、修改成功终态等非法状态转换。"""
        if target not in cls.ALLOWED_TRANSITIONS[current]:
            raise ValueError(
                f"不允许的摘要任务状态转换：{current.value} -> {target.value}"
            )
