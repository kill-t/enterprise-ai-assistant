import hashlib
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.infrastructure.object_storage.base import BaseObjectStorage
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.document_repository import DocumentRepository
from app.modules.knowledge.repository.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from app.modules.knowledge.schema.document_schema import (
    DocumentResponse,
    DocumentUploadResponse,
)


READ_CHUNK_SIZE = 1024 * 1024
SUPPORTED_DOCUMENT_TYPES: dict[str, tuple[str, set[str]]] = {
    ".pdf": ("application/pdf", {"application/pdf", "application/octet-stream"}),
    ".txt": ("text/plain", {"text/plain", "application/octet-stream"}),
    ".md": (
        "text/markdown",
        {"text/markdown", "text/plain", "application/octet-stream"},
    ),
    ".markdown": (
        "text/markdown",
        {"text/markdown", "text/plain", "application/octet-stream"},
    ),
}


class UploadSource(Protocol):
    """描述 Service 需要的上传文件最小接口，避免绑定具体 HTTP 类。"""

    filename: str | None
    content_type: str | None
    file: BinaryIO

    async def read(self, size: int = -1) -> bytes:
        """异步读取指定数量字节。"""
        ...

    async def seek(self, offset: int) -> None:
        """把上传流移动到指定偏移。"""
        ...


class IngestionDispatchService(Protocol):
    """描述上传成功后需要的最小持久化任务投递能力。"""

    async def dispatch_if_due(self, job_id: UUID) -> str:
        """尝试投递任务；Broker 失败时由实现保留 PENDING 状态。"""
        ...


class DocumentService:
    """编排文档校验、对象存储、持久化任务和失败补偿。"""

    def __init__(
        self,
        db: AsyncSession,
        object_storage: BaseObjectStorage,
        max_file_size_bytes: int,
        ingestion_dispatch_service: IngestionDispatchService | None = None,
    ) -> None:
        """组装当前请求共用的数据库和对象存储依赖。"""
        self.db = db
        self.object_storage = object_storage
        self.max_file_size_bytes = max_file_size_bytes
        self.ingestion_dispatch_service = ingestion_dispatch_service
        self.knowledge_base_repository = KnowledgeBaseRepository(db)
        self.repository = DocumentRepository(db)

    async def upload(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        upload: UploadSource,
    ) -> DocumentUploadResponse:
        """校验并保存原文件，再原子创建文档与待处理入库任务。"""
        knowledge_base = await self._get_owned_knowledge_base(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )
        if knowledge_base.is_archived:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_ARCHIVED,
                message="知识库已归档，无法上传文档",
                status_code=409,
            )

        filename, extension, mime_type = self._validate_metadata(upload)
        size_bytes, sha256 = await self._calculate_size_and_sha256(upload)
        existing = await self.repository.get_by_sha256_for_knowledge_base(
            knowledge_base_id=knowledge_base_id,
            sha256=sha256,
        )
        if existing is not None:
            raise self._duplicate_exception()

        document_id = uuid4()
        job_id = uuid4()
        object_key = self._build_object_key(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            extension=extension,
        )
        await upload.seek(0)
        await self._store_object(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            object_key=object_key,
            data=upload.file,
            length=size_bytes,
            content_type=mime_type,
        )

        try:
            document, job = await self.repository.create_with_job(
                document_id=document_id,
                job_id=job_id,
                knowledge_base_id=knowledge_base_id,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256,
                object_key=object_key,
            )
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            await self._compensate_object(object_key)
            if self._is_duplicate_constraint(exc):
                raise self._duplicate_exception() from exc
            raise
        except Exception:
            await self.db.rollback()
            await self._compensate_object(object_key)
            raise

        app_logger.info(
            "文档上传已接收 user_id=%s knowledge_base_id=%s document_id=%s size=%s",
            user_id,
            knowledge_base_id,
            document.id,
            size_bytes,
        )
        await self._dispatch_ingestion_safely(job.id)
        return DocumentUploadResponse(
            document=DocumentResponse.model_validate(document),
            job_id=job.id,
            job_status=job.status,
        )

    async def _dispatch_ingestion_safely(self, job_id: UUID) -> None:
        """提交上传后尽力投递；调度异常不能伪装成文件上传失败。"""
        if self.ingestion_dispatch_service is None:
            return
        try:
            result = await self.ingestion_dispatch_service.dispatch_if_due(job_id)
            if result != "dispatched":
                app_logger.warning(
                    "文档入库任务暂未投递，等待恢复扫描 job_id=%s result=%s",
                    job_id,
                    result,
                )
        except Exception:
            # 文件和 PENDING 任务已经提交；回滚这里只清理调度阶段失败的事务状态。
            await self.db.rollback()
            app_logger.exception(
                "文档入库任务调度异常，等待恢复扫描 job_id=%s",
                job_id,
            )

    async def list_documents(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> list[DocumentResponse]:
        """校验知识库所有权后返回可轮询的文档状态列表。"""
        await self._get_owned_knowledge_base(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
        )
        documents = await self.repository.list_by_knowledge_base_for_user(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
        )
        return [DocumentResponse.model_validate(document) for document in documents]

    async def get_document(
        self,
        user_id: UUID,
        document_id: UUID,
    ) -> DocumentResponse:
        """返回当前用户拥有的文档状态，并隐藏跨用户资源存在性。"""
        document = await self.repository.get_by_id_for_user(document_id, user_id)
        if document is None:
            raise BusinessException(
                code=ErrorCode.DOCUMENT_NOT_FOUND,
                message="文档不存在",
                status_code=404,
            )
        return DocumentResponse.model_validate(document)

    async def _get_owned_knowledge_base(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
    ) -> KnowledgeBaseEntity:
        """按主键和用户查询知识库，不单独暴露越权原因。"""
        knowledge_base = await self.knowledge_base_repository.get_by_id_for_user(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
        )
        if knowledge_base is None:
            raise BusinessException(
                code=ErrorCode.KNOWLEDGE_BASE_NOT_FOUND,
                message="知识库不存在",
                status_code=404,
            )
        return knowledge_base

    def _validate_metadata(self, upload: UploadSource) -> tuple[str, str, str]:
        """清理文件名，并验证扩展名和客户端 MIME 类型是否匹配。"""
        raw_filename = (upload.filename or "").strip()
        # 同时处理浏览器可能提交的 Windows 与 POSIX 路径，只保留最终文件名。
        filename = raw_filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        if not filename or len(filename) > 512:
            raise BusinessException(
                code=ErrorCode.DOCUMENT_FILENAME_INVALID,
                message="文件名不能为空且不能超过 512 个字符",
                status_code=422,
            )

        extension = Path(filename).suffix.lower()
        type_config = SUPPORTED_DOCUMENT_TYPES.get(extension)
        request_mime_type = upload.content_type or "application/octet-stream"
        request_mime_type = request_mime_type.split(";", maxsplit=1)[0].lower()
        if type_config is None or request_mime_type not in type_config[1]:
            raise BusinessException(
                code=ErrorCode.DOCUMENT_TYPE_NOT_SUPPORTED,
                message="仅支持 PDF、TXT 和 Markdown 文件",
                status_code=415,
            )
        canonical_mime_type = type_config[0]
        return filename, extension, canonical_mime_type

    async def _calculate_size_and_sha256(
        self,
        upload: UploadSource,
    ) -> tuple[int, str]:
        """分块读取上传流，计算真实大小和 SHA-256，避免一次载入全部内存。"""
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            while chunk := await upload.read(READ_CHUNK_SIZE):
                size_bytes += len(chunk)
                if size_bytes > self.max_file_size_bytes:
                    raise BusinessException(
                        code=ErrorCode.DOCUMENT_TOO_LARGE,
                        message="单个文件不能超过 20 MB",
                        status_code=413,
                    )
                digest.update(chunk)
        except BusinessException:
            raise
        except Exception as exc:
            raise BusinessException(
                code=ErrorCode.DOCUMENT_READ_FAILED,
                message="读取上传文件失败",
                status_code=422,
            ) from exc

        if size_bytes == 0:
            raise BusinessException(
                code=ErrorCode.DOCUMENT_EMPTY,
                message="不能上传空文件",
                status_code=422,
            )
        return size_bytes, digest.hexdigest()

    async def _store_object(
        self,
        user_id: UUID,
        knowledge_base_id: UUID,
        object_key: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> None:
        """保存原文件，并把基础设施异常转换为稳定的 503。"""
        try:
            await self.object_storage.ensure_bucket()
            await self.object_storage.put_object(
                object_key=object_key,
                data=data,
                length=length,
                content_type=content_type,
            )
        except Exception as exc:
            app_logger.warning(
                "对象存储上传失败 user_id=%s knowledge_base_id=%s",
                user_id,
                knowledge_base_id,
                exc_info=True,
            )
            raise BusinessException(
                code=ErrorCode.OBJECT_STORAGE_UNAVAILABLE,
                message="文件存储服务暂时不可用，请稍后重试",
                status_code=503,
            ) from exc

    async def _compensate_object(self, object_key: str) -> None:
        """数据库失败后尽力删除已上传对象，且不覆盖最初异常。"""
        try:
            await self.object_storage.delete_object(object_key)
            app_logger.info("已补偿删除未入库对象 object_key=%s", object_key)
        except Exception:
            app_logger.exception(
                "补偿删除对象失败，需后续清理 object_key=%s",
                object_key,
            )

    @staticmethod
    def _build_object_key(
        user_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        extension: str,
    ) -> str:
        """用服务端 UUID 构造不可由文件名逃逸的对象键。"""
        return (
            f"users/{user_id}/knowledge-bases/{knowledge_base_id}/"
            f"documents/{document_id}{extension}"
        )

    @staticmethod
    def _is_duplicate_constraint(exc: IntegrityError) -> bool:
        """识别数据库报告的同知识库文档哈希唯一约束。"""
        diagnostic = getattr(exc.orig, "diag", None)
        return (
            getattr(diagnostic, "constraint_name", None)
            == "uq_documents_knowledge_base_sha256"
        )

    @staticmethod
    def _duplicate_exception() -> BusinessException:
        """构造前端可稳定识别的重复文档异常。"""
        return BusinessException(
            code=ErrorCode.DOCUMENT_ALREADY_EXISTS,
            message="该知识库中已存在内容相同的文档",
            status_code=409,
        )
