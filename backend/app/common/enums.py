from enum import Enum


class MessageRole(str, Enum):
    """消息在对话协议中的角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageStatus(str, Enum):
    """助手消息从创建到完成、失败或取消的生命周期状态。"""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SummaryJobStatus(str, Enum):
    """会话摘要后台任务的持久化生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DocumentStatus(str, Enum):
    """文档从等待入库到可检索或失败的持久化生命周期。"""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentIngestionJobStatus(str, Enum):
    """文档入库任务在 Celery 投递和执行过程中的持久化状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
