import asyncio
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.common.enums import DocumentIngestionJobStatus, DocumentStatus
from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.main import create_app
from app.modules.identity.dependencies import get_current_user
from app.modules.knowledge.dependencies import get_document_service
from app.modules.knowledge.entity.document import DocumentEntity
from app.modules.knowledge.entity.document_ingestion_job import (
    DocumentIngestionJobEntity,
)
from app.modules.knowledge.entity.knowledge_base import KnowledgeBaseEntity
from app.modules.knowledge.repository.document_repository import DocumentRepository
from app.modules.knowledge.schema.document_schema import (
    DocumentResponse,
    DocumentRetryResponse,
    DocumentUploadResponse,
)
from app.modules.knowledge.service.document_service import DocumentService


class FakeUpload:
    """模拟 FastAPI UploadFile 的异步读取和同步底层文件接口。"""

    def __init__(
        self,
        content: bytes,
        filename: str = "notes.txt",
        content_type: str = "text/plain",
    ) -> None:
        """保存测试文件元数据，并使用内存流模拟临时文件。"""
        self.filename = filename
        self.content_type = content_type
        self.file = BytesIO(content)
        self.read_calls = 0

    async def read(self, size: int = -1) -> bytes:
        """按 UploadFile 相同方式异步读取底层字节。"""
        self.read_calls += 1
        return self.file.read(size)

    async def seek(self, offset: int) -> None:
        """将底层流移动到指定位置，供对象存储重新读取。"""
        self.file.seek(offset)


def test_upload_creates_object_document_and_pending_job_atomically() -> None:
    """合法上传应保存对象，并在一次数据库事务中创建文档和待处理任务。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, job = build_document_and_job(knowledge_base_id=knowledge_base.id)
    events: list[str] = []
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
        rollback=AsyncMock(),
    )
    storage = build_storage()
    dispatch_service = SimpleNamespace(
        dispatch_if_due=AsyncMock(
            side_effect=lambda job_id: events.append("dispatch") or "dispatched"
        )
    )
    service = build_service(
        db=db,
        storage=storage,
        dispatch_service=dispatch_service,
    )
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=None),
        create_with_job=AsyncMock(return_value=(document, job)),
    )
    upload = FakeUpload(b"meeting notes")

    response = asyncio.run(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=upload,
        )
    )

    assert response.document.id == document.id
    assert response.job_id == job.id
    assert response.job_status == DocumentIngestionJobStatus.PENDING
    storage.ensure_bucket.assert_awaited_once_with()
    put_kwargs = storage.put_object.await_args.kwargs
    assert put_kwargs["length"] == len(b"meeting notes")
    assert put_kwargs["content_type"] == "text/plain"
    assert put_kwargs["data"].tell() == 0
    assert str(user_id) in put_kwargs["object_key"]
    assert str(knowledge_base.id) in put_kwargs["object_key"]
    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()
    dispatch_service.dispatch_if_due.assert_awaited_once_with(job.id)
    assert events == ["commit", "dispatch"]


def test_upload_remains_successful_when_scheduling_raises_after_commit() -> None:
    """数据库已提交后，即使调度内部异常也应保留 PENDING 任务并返回上传成功。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, job = build_document_and_job(knowledge_base_id=knowledge_base.id)
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    dispatch_service = SimpleNamespace(
        dispatch_if_due=AsyncMock(side_effect=RuntimeError("调度数据库异常"))
    )
    service = build_service(
        db=db,
        dispatch_service=dispatch_service,
    )
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=None),
        create_with_job=AsyncMock(return_value=(document, job)),
    )

    response = asyncio.run(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=FakeUpload(b"meeting notes"),
        )
    )

    assert response.job_id == job.id
    assert response.job_status == DocumentIngestionJobStatus.PENDING
    db.commit.assert_awaited_once_with()
    db.rollback.assert_awaited_once_with()


def test_upload_rejects_archived_knowledge_base_before_reading_file() -> None:
    """归档知识库不能接收新文件，并且应在读取大文件之前尽早失败。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id, is_archived=True)
    storage = build_storage()
    service = build_service(storage=storage)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    upload = FakeUpload(b"meeting notes")

    error = run_business_error(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=upload,
        )
    )

    assert error.code == ErrorCode.KNOWLEDGE_BASE_ARCHIVED
    assert error.status_code == 409
    assert upload.read_calls == 0
    storage.put_object.assert_not_awaited()


@pytest.mark.parametrize(
    ("upload", "expected_code", "expected_status"),
    [
        (FakeUpload(b""), ErrorCode.DOCUMENT_EMPTY, 422),
        (
            FakeUpload(b"123456", filename="notes.txt"),
            ErrorCode.DOCUMENT_TOO_LARGE,
            413,
        ),
        (
            FakeUpload(
                b"image",
                filename="photo.bmp",
                content_type="image/bmp",
            ),
            ErrorCode.DOCUMENT_TYPE_NOT_SUPPORTED,
            415,
        ),
        (
            FakeUpload(
                b"not pdf",
                filename="manual.pdf",
                content_type="text/plain",
            ),
            ErrorCode.DOCUMENT_TYPE_NOT_SUPPORTED,
            415,
        ),
    ],
)
def test_upload_rejects_invalid_file(
    upload: FakeUpload,
    expected_code: ErrorCode,
    expected_status: int,
) -> None:
    """空文件、超限文件以及扩展名或 MIME 不支持的文件必须在上传前失败。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    storage = build_storage()
    service = build_service(storage=storage, max_file_size_bytes=5)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )

    error = run_business_error(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=upload,
        )
    )

    assert error.code == expected_code
    assert error.status_code == expected_status
    storage.ensure_bucket.assert_not_awaited()
    storage.put_object.assert_not_awaited()


def test_upload_accepts_docx_with_canonical_mime_type() -> None:
    """Word 上传应接受标准 MIME，并统一保存标准 OOXML MIME。"""
    service = build_service()
    upload = FakeUpload(
        b"docx",
        filename="项目方案.DOCX",
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

    filename, extension, mime_type = service._validate_metadata(upload)

    assert filename == "项目方案.DOCX"
    assert extension == ".docx"
    assert mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_upload_accepts_xlsx_with_canonical_mime_type() -> None:
    """Excel 上传应接受标准 MIME，并统一保存标准 OOXML MIME。"""
    service = build_service()
    upload = FakeUpload(
        b"xlsx",
        filename="学生名单.XLSX",
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    filename, extension, mime_type = service._validate_metadata(upload)

    assert filename == "学生名单.XLSX"
    assert extension == ".xlsx"
    assert mime_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_mime_type"),
    [
        ("扫描件.PNG", "image/png", "image/png"),
        ("票据.jpg", "image/jpeg", "image/jpeg"),
        ("白板.JPEG", "application/octet-stream", "image/jpeg"),
    ],
)
def test_upload_accepts_ocr_image_types(
    filename: str,
    content_type: str,
    expected_mime_type: str,
) -> None:
    """PNG 与 JPEG 图片应通过上传白名单并保存标准 MIME。"""
    service = build_service()
    upload = FakeUpload(
        b"image",
        filename=filename,
        content_type=content_type,
    )

    normalized_filename, extension, mime_type = service._validate_metadata(upload)

    assert normalized_filename == filename
    assert extension in {".png", ".jpg", ".jpeg"}
    assert mime_type == expected_mime_type



def test_upload_rejects_duplicate_sha256_before_object_write() -> None:
    """同一知识库已有相同内容时，应在写入 MinIO 前返回稳定 409。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    existing, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    storage = build_storage()
    service = build_service(storage=storage)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=existing)
    )

    error = run_business_error(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=FakeUpload(b"same content"),
        )
    )

    assert error.code == ErrorCode.DOCUMENT_ALREADY_EXISTS
    assert error.status_code == 409
    storage.put_object.assert_not_awaited()


def test_storage_failure_does_not_write_database() -> None:
    """MinIO 上传失败时数据库中不能出现指向不存在对象的文档记录。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    storage = build_storage()
    storage.put_object.side_effect = ConnectionError("MinIO unavailable")
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db, storage=storage)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=None),
        create_with_job=AsyncMock(),
    )

    error = run_business_error(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=FakeUpload(b"meeting notes"),
        )
    )

    assert error.code == ErrorCode.OBJECT_STORAGE_UNAVAILABLE
    assert error.status_code == 503
    service.repository.create_with_job.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_database_failure_rolls_back_and_compensates_object() -> None:
    """对象已上传但数据库写入失败时，必须回滚并删除刚上传的对象。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    storage = build_storage()
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db, storage=storage)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=None),
        create_with_job=AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.upload(
                user_id=user_id,
                knowledge_base_id=knowledge_base.id,
                upload=FakeUpload(b"meeting notes"),
            )
        )

    db.rollback.assert_awaited_once_with()
    uploaded_key = storage.put_object.await_args.kwargs["object_key"]
    storage.delete_object.assert_awaited_once_with(uploaded_key)
    db.commit.assert_not_awaited()


def test_concurrent_duplicate_compensates_object_and_returns_409() -> None:
    """并发上传越过预检查后，数据库唯一约束仍应去重并清理本次对象。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    storage = build_storage()
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db, storage=storage)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    unique_violation = Exception("unique")
    unique_violation.diag = SimpleNamespace(
        constraint_name="uq_documents_knowledge_base_sha256"
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=None),
        create_with_job=AsyncMock(
            side_effect=IntegrityError("INSERT", {}, unique_violation)
        ),
    )

    error = run_business_error(
        service.upload(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            upload=FakeUpload(b"same content"),
        )
    )

    assert error.code == ErrorCode.DOCUMENT_ALREADY_EXISTS
    assert error.status_code == 409
    db.rollback.assert_awaited_once_with()
    uploaded_key = storage.put_object.await_args.kwargs["object_key"]
    storage.delete_object.assert_awaited_once_with(uploaded_key)


def test_compensation_failure_keeps_original_database_error() -> None:
    """补偿删除失败只能记录日志，不能覆盖最初的数据库异常。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    storage = build_storage()
    storage.delete_object.side_effect = ConnectionError("delete unavailable")
    service = build_service(storage=storage)
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        get_by_sha256_for_knowledge_base=AsyncMock(return_value=None),
        create_with_job=AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(
            service.upload(
                user_id=user_id,
                knowledge_base_id=knowledge_base.id,
                upload=FakeUpload(b"meeting notes"),
            )
        )

    storage.delete_object.assert_awaited_once()


def test_repository_document_lookup_filters_document_and_owner() -> None:
    """文档详情 SQL 必须通过知识库关系同时过滤文档 ID 和当前用户 ID。"""
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    repository = DocumentRepository(db)

    asyncio.run(repository.get_by_id_for_user(uuid4(), uuid4()))

    statement = db.scalar.await_args.args[0]
    sql = str(statement)
    assert "documents.id" in sql
    assert "knowledge_bases.user_id" in sql
    assert "JOIN knowledge_bases" in sql


def test_service_lists_documents_only_after_knowledge_base_ownership_check() -> None:
    """状态列表应先校验知识库所有权，再查询属于该用户的文档。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    service = build_service()
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )
    service.repository = SimpleNamespace(
        list_by_knowledge_base_for_user=AsyncMock(return_value=[document])
    )

    response = asyncio.run(
        service.list_documents(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
        )
    )

    assert response[0].id == document.id
    service.repository.list_by_knowledge_base_for_user.assert_awaited_once_with(
        knowledge_base_id=knowledge_base.id,
        user_id=user_id,
    )


def test_service_hides_foreign_document_as_not_found() -> None:
    """不存在和属于其他用户的文档必须返回相同的 404。"""
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=None)
    )

    error = run_business_error(
        service.get_document(user_id=uuid4(), document_id=uuid4())
    )

    assert error.code == ErrorCode.DOCUMENT_NOT_FOUND
    assert error.status_code == 404


def test_failed_document_retry_creates_new_job_commits_then_dispatches() -> None:
    """手动重试应保留旧任务历史，新建任务并在提交后尽力投递。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, old_job = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = DocumentStatus.FAILED
    old_job.status = DocumentIngestionJobStatus.FAILED
    _, new_job = build_document_and_job(knowledge_base_id=knowledge_base.id)
    new_job.document_id = document.id
    new_job.idempotency_key = f"document-ingestion:{document.id}:manual:{new_job.id}"
    events: list[str] = []
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
        rollback=AsyncMock(),
    )
    dispatch_service = SimpleNamespace(
        dispatch_if_due=AsyncMock(
            side_effect=lambda job_id: events.append("dispatch") or "dispatched"
        )
    )
    service = build_service(db=db, dispatch_service=dispatch_service)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=document),
        retry_failed_with_job=AsyncMock(return_value=(document, new_job)),
    )
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )

    response = asyncio.run(
        service.retry_document(user_id=user_id, document_id=document.id)
    )

    assert response.document.id == document.id
    assert response.job_id == new_job.id
    assert response.job_status == DocumentIngestionJobStatus.PENDING
    service.repository.retry_failed_with_job.assert_awaited_once()
    dispatch_service.dispatch_if_due.assert_awaited_once_with(new_job.id)
    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()
    assert events == ["commit", "dispatch"]


@pytest.mark.parametrize(
    "document_status",
    [DocumentStatus.PENDING, DocumentStatus.PROCESSING, DocumentStatus.READY],
)
def test_retry_rejects_document_that_is_not_failed(
    document_status: DocumentStatus,
) -> None:
    """等待、处理中和已完成文档都不能创建没有意义的手动重试任务。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = document_status
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=document),
        retry_failed_with_job=AsyncMock(),
    )
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )

    error = run_business_error(
        service.retry_document(user_id=user_id, document_id=document.id)
    )

    assert error.code == ErrorCode.DOCUMENT_NOT_RETRYABLE
    assert error.status_code == 409
    service.repository.retry_failed_with_job.assert_not_awaited()


def test_retry_rejects_document_in_archived_knowledge_base() -> None:
    """已归档知识库不能重新启动入库任务，应先恢复知识库。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id, is_archived=True)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = DocumentStatus.FAILED
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=document),
        retry_failed_with_job=AsyncMock(),
    )
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )

    error = run_business_error(
        service.retry_document(user_id=user_id, document_id=document.id)
    )

    assert error.code == ErrorCode.KNOWLEDGE_BASE_ARCHIVED
    service.repository.retry_failed_with_job.assert_not_awaited()


def test_concurrent_retry_loser_rolls_back_and_returns_not_retryable() -> None:
    """预检查后状态被其他请求抢先修改时，条件更新失败者必须回滚并返回 409。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = DocumentStatus.FAILED
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    dispatch_service = SimpleNamespace(dispatch_if_due=AsyncMock())
    service = build_service(db=db, dispatch_service=dispatch_service)
    service.repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=document),
        retry_failed_with_job=AsyncMock(return_value=None),
    )
    service.knowledge_base_repository = SimpleNamespace(
        get_by_id_for_user=AsyncMock(return_value=knowledge_base)
    )

    error = run_business_error(
        service.retry_document(user_id=user_id, document_id=document.id)
    )

    assert error.code == ErrorCode.DOCUMENT_NOT_RETRYABLE
    db.rollback.assert_awaited_once_with()
    db.commit.assert_not_awaited()
    dispatch_service.dispatch_if_due.assert_not_awaited()


def test_repository_retry_uses_failed_condition_and_adds_new_job() -> None:
    """Repository 必须用 FAILED 条件更新，并为本次手动操作保留独立任务记录。"""
    document, _ = build_document_and_job(knowledge_base_id=uuid4())
    document.status = DocumentStatus.PENDING
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=document),
        add=Mock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = DocumentRepository(db)
    job_id = uuid4()

    result = asyncio.run(
        repository.retry_failed_with_job(
            document_id=document.id,
            job_id=job_id,
        )
    )

    statement = db.scalar.await_args.args[0]
    compiled = statement.compile()
    assert "UPDATE documents" in str(statement)
    assert "documents.status" in str(statement)
    assert DocumentStatus.FAILED in compiled.params.values()
    assert DocumentStatus.PENDING in compiled.params.values()
    job = db.add.call_args.args[0]
    assert job.id == job_id
    assert job.document_id == document.id
    assert str(job_id) in job.idempotency_key
    assert result == (document, job)
    db.flush.assert_awaited_once_with()


def test_delete_uses_tombstone_then_removes_object_and_database_record() -> None:
    """删除必须先提交墓碑，再清理 MinIO，最后释放数据库唯一约束。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = DocumentStatus.READY
    deleted_document, _ = build_document_and_job(
        knowledge_base_id=knowledge_base.id
    )
    deleted_document.id = document.id
    deleted_document.object_key = document.object_key
    deleted_document.status = DocumentStatus.DELETED
    events: list[str] = []
    db = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: events.append("commit")),
        rollback=AsyncMock(),
    )
    storage = build_storage()
    storage.delete_object.side_effect = lambda object_key: events.append("object")
    service = build_service(db=db, storage=storage)
    service.repository = SimpleNamespace(
        get_by_id_for_user_including_deleted=AsyncMock(return_value=document),
        mark_deleted_if_terminal=AsyncMock(
            side_effect=lambda **kwargs: events.append("mark") or deleted_document
        ),
        hard_delete_if_marked=AsyncMock(
            side_effect=lambda **kwargs: events.append("hard") or document.id
        ),
    )

    asyncio.run(service.delete_document(user_id=user_id, document_id=document.id))

    service.repository.mark_deleted_if_terminal.assert_awaited_once_with(
        document_id=document.id
    )
    assert db.commit.await_count == 2
    db.rollback.assert_not_awaited()
    storage.delete_object.assert_awaited_once_with(document.object_key)
    service.repository.hard_delete_if_marked.assert_awaited_once_with(
        document_id=document.id
    )
    assert events == ["mark", "commit", "object", "hard", "commit"]


@pytest.mark.parametrize(
    "document_status",
    [DocumentStatus.PENDING, DocumentStatus.PROCESSING],
)
def test_delete_rejects_document_with_active_ingestion(
    document_status: DocumentStatus,
) -> None:
    """活动任务没有取消协议时必须拒绝删除，避免迟到 Worker 覆盖删除状态。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = document_status
    service = build_service()
    service.repository = SimpleNamespace(
        get_by_id_for_user_including_deleted=AsyncMock(return_value=document),
        mark_deleted_if_terminal=AsyncMock(),
    )

    error = run_business_error(
        service.delete_document(user_id=user_id, document_id=document.id)
    )

    assert error.code == ErrorCode.DOCUMENT_BUSY
    assert error.status_code == 409
    service.repository.mark_deleted_if_terminal.assert_not_awaited()


def test_delete_storage_failure_keeps_committed_tombstone_for_retry() -> None:
    """MinIO 不可用时文档先从业务查询隐藏，且不能提前硬删墓碑。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = DocumentStatus.FAILED
    deleted_document, _ = build_document_and_job(
        knowledge_base_id=knowledge_base.id
    )
    deleted_document.id = document.id
    deleted_document.status = DocumentStatus.DELETED
    storage = build_storage()
    storage.delete_object.side_effect = ConnectionError("MinIO unavailable")
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = build_service(db=db, storage=storage)
    service.repository = SimpleNamespace(
        get_by_id_for_user_including_deleted=AsyncMock(return_value=document),
        mark_deleted_if_terminal=AsyncMock(return_value=deleted_document),
        hard_delete_if_marked=AsyncMock(),
    )

    error = run_business_error(
        service.delete_document(user_id=user_id, document_id=document.id)
    )

    assert error.code == ErrorCode.OBJECT_STORAGE_UNAVAILABLE
    assert error.status_code == 503
    db.commit.assert_awaited_once_with()
    service.repository.hard_delete_if_marked.assert_not_awaited()


def test_repeated_delete_resumes_from_existing_tombstone() -> None:
    """上次清理中断后，重复 DELETE 应跳过状态转换并继续对象与墓碑清理。"""
    user_id = uuid4()
    knowledge_base = build_knowledge_base(user_id=user_id)
    document, _ = build_document_and_job(knowledge_base_id=knowledge_base.id)
    document.status = DocumentStatus.DELETED
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    storage = build_storage()
    service = build_service(db=db, storage=storage)
    service.repository = SimpleNamespace(
        get_by_id_for_user_including_deleted=AsyncMock(return_value=document),
        mark_deleted_if_terminal=AsyncMock(),
        hard_delete_if_marked=AsyncMock(return_value=document.id),
    )

    asyncio.run(service.delete_document(user_id=user_id, document_id=document.id))

    service.repository.mark_deleted_if_terminal.assert_not_awaited()
    storage.delete_object.assert_awaited_once_with(document.object_key)
    service.repository.hard_delete_if_marked.assert_awaited_once_with(
        document_id=document.id
    )
    assert db.commit.await_count == 2


def test_repository_delete_only_updates_ready_or_failed_document() -> None:
    """逻辑删除 SQL 必须带终态条件，迟到或并发状态变化不能被覆盖。"""
    document, _ = build_document_and_job(knowledge_base_id=uuid4())
    document.status = DocumentStatus.DELETED
    db = SimpleNamespace(scalar=AsyncMock(return_value=document))
    repository = DocumentRepository(db)

    result = asyncio.run(
        repository.mark_deleted_if_terminal(document_id=document.id)
    )

    statement = db.scalar.await_args.args[0]
    compiled = statement.compile()
    sql = str(statement)
    assert "UPDATE documents" in sql
    assert "documents.status IN" in sql
    terminal_statuses = next(
        value for value in compiled.params.values() if isinstance(value, list)
    )
    assert terminal_statuses == [DocumentStatus.READY, DocumentStatus.FAILED]
    assert DocumentStatus.DELETED in compiled.params.values()
    assert result is document


def test_repository_hard_delete_requires_deleted_tombstone() -> None:
    """最终硬删除必须只接受 DELETED 状态，不能跳过跨存储恢复点。"""
    document_id = uuid4()
    db = SimpleNamespace(scalar=AsyncMock(return_value=document_id))
    repository = DocumentRepository(db)

    result = asyncio.run(repository.hard_delete_if_marked(document_id))

    statement = db.scalar.await_args.args[0]
    compiled = statement.compile()
    sql = str(statement)
    assert "DELETE FROM documents" in sql
    assert "documents.status" in sql
    assert DocumentStatus.DELETED in compiled.params.values()
    assert result == document_id


def test_document_upload_http_endpoint_uses_multipart_and_returns_202() -> None:
    """真实 ASGI 路由应接收 multipart 文件并返回异步入库的 202 契约。"""
    current_user = SimpleNamespace(id=uuid4())
    knowledge_base_id = uuid4()
    document, job = build_document_and_job(knowledge_base_id=knowledge_base_id)
    expected = DocumentUploadResponse(
        document=DocumentResponse.model_validate(document),
        job_id=job.id,
        job_status=job.status,
    )
    service = SimpleNamespace(upload=AsyncMock(return_value=expected))
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_document_service] = lambda: service

    async def scenario() -> tuple[int, dict]:
        """向内存 FastAPI 应用发送真实 multipart/form-data 请求。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
                files={"file": ("notes.txt", b"meeting notes", "text/plain")},
            )
        return response.status_code, response.json()

    try:
        status_code, payload = asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()

    assert status_code == 202
    assert payload["document"]["id"] == str(document.id)
    assert payload["job_status"] == DocumentIngestionJobStatus.PENDING.value
    upload_call = service.upload.await_args.kwargs
    assert upload_call["user_id"] == current_user.id
    assert upload_call["knowledge_base_id"] == knowledge_base_id
    assert upload_call["upload"].filename == "notes.txt"


def test_document_retry_and_delete_http_endpoints_use_lifecycle_contracts() -> None:
    """重试返回新任务的 202，逻辑删除返回没有正文的 204。"""
    current_user = SimpleNamespace(id=uuid4())
    knowledge_base_id = uuid4()
    document, job = build_document_and_job(knowledge_base_id=knowledge_base_id)
    expected = DocumentRetryResponse(
        document=DocumentResponse.model_validate(document),
        job_id=job.id,
        job_status=job.status,
    )
    service = SimpleNamespace(
        retry_document=AsyncMock(return_value=expected),
        delete_document=AsyncMock(),
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_document_service] = lambda: service

    async def scenario() -> tuple[int, dict, int, bytes]:
        """通过真实 ASGI 路由验证方法、状态码和空响应。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            retry_response = await client.post(
                f"/api/v1/documents/{document.id}/retry"
            )
            delete_response = await client.delete(
                f"/api/v1/documents/{document.id}"
            )
        return (
            retry_response.status_code,
            retry_response.json(),
            delete_response.status_code,
            delete_response.content,
        )

    try:
        retry_status, retry_payload, delete_status, delete_content = asyncio.run(
            scenario()
        )
    finally:
        app.dependency_overrides.clear()

    assert retry_status == 202
    assert retry_payload["job_id"] == str(job.id)
    assert delete_status == 204
    assert delete_content == b""
    service.retry_document.assert_awaited_once_with(
        user_id=current_user.id,
        document_id=document.id,
    )
    service.delete_document.assert_awaited_once_with(
        user_id=current_user.id,
        document_id=document.id,
    )


def test_document_openapi_paths_are_protected() -> None:
    """上传、查询、重试和删除接口必须声明 Bearer Token 安全要求。"""
    paths = create_app().openapi()["paths"]
    collection = paths["/api/v1/knowledge-bases/{knowledge_base_id}/documents"]
    detail = paths["/api/v1/documents/{document_id}"]

    assert {"get", "post"}.issubset(collection)
    assert {"get", "delete"}.issubset(detail)
    retry = paths["/api/v1/documents/{document_id}/retry"]
    assert collection["post"]["security"]
    assert detail["get"]["security"]
    assert detail["delete"]["security"]
    assert retry["post"]["security"]


def build_service(
    db=None,
    storage=None,
    max_file_size_bytes: int = 20 * 1024 * 1024,
    dispatch_service=None,
) -> DocumentService:
    """构造不连接真实基础设施的文档 Service 测试对象。"""
    return DocumentService(
        db=db or SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
        object_storage=storage or build_storage(),
        max_file_size_bytes=max_file_size_bytes,
        ingestion_dispatch_service=dispatch_service,
    )


def build_storage() -> SimpleNamespace:
    """构造记录调用参数的异步对象存储替身。"""
    return SimpleNamespace(
        ensure_bucket=AsyncMock(),
        put_object=AsyncMock(),
        delete_object=AsyncMock(),
    )


def build_knowledge_base(
    user_id: UUID,
    is_archived: bool = False,
) -> KnowledgeBaseEntity:
    """构造字段完整的知识库实体。"""
    now = datetime.now(timezone.utc)
    return KnowledgeBaseEntity(
        id=uuid4(),
        user_id=user_id,
        name="项目资料",
        description=None,
        embedding_provider="mock",
        embedding_model="mock-hash-1024",
        embedding_dimension=1024,
        is_archived=is_archived,
        created_at=now,
        updated_at=now,
    )


def build_document_and_job(
    knowledge_base_id: UUID,
) -> tuple[DocumentEntity, DocumentIngestionJobEntity]:
    """构造可以直接转换成上传响应的文档和入库任务实体。"""
    now = datetime.now(timezone.utc)
    document = DocumentEntity(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        filename="notes.txt",
        mime_type="text/plain",
        size_bytes=13,
        sha256="a" * 64,
        object_key=f"documents/{uuid4()}.txt",
        status=DocumentStatus.PENDING,
        parser_version=None,
        chunker_version=None,
        page_count=0,
        chunk_count=0,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        finished_at=None,
    )
    job = DocumentIngestionJobEntity(
        id=uuid4(),
        document_id=document.id,
        idempotency_key=f"document-ingestion:{document.id}:v1",
        celery_task_id=None,
        status=DocumentIngestionJobStatus.PENDING,
        attempts=0,
        max_attempts=3,
        error_code=None,
        error_message=None,
        started_at=None,
        finished_at=None,
        next_retry_at=None,
        dispatch_claimed_at=None,
        created_at=now,
        updated_at=now,
    )
    return document, job


def run_business_error(awaitable) -> BusinessException:
    """运行异步业务操作并返回预期的 BusinessException。"""

    async def scenario() -> BusinessException:
        """捕获业务异常，未抛出时主动让测试失败。"""
        try:
            await awaitable
        except BusinessException as exc:
            return exc
        raise AssertionError("操作必须抛出业务异常")

    return asyncio.run(scenario())
