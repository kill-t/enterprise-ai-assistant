from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.common.enums import DocumentIngestionJobStatus, DocumentStatus


class DocumentResponse(BaseModel):
    """返回文档来源、入库状态和可追溯处理统计。"""

    id: UUID
    knowledge_base_id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    parser_version: str | None
    chunker_version: str | None
    page_count: int
    chunk_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class DocumentUploadResponse(BaseModel):
    """表示文件已接收，并已创建可恢复的异步入库任务。"""

    document: DocumentResponse
    job_id: UUID
    job_status: DocumentIngestionJobStatus
