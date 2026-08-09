import asyncio
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
                filename="photo.png",
                content_type="image/png",
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


def test_document_openapi_paths_are_protected() -> None:
    """上传、列表和状态详情接口必须声明 Bearer Token 安全要求。"""
    paths = create_app().openapi()["paths"]
    collection = paths["/api/v1/knowledge-bases/{knowledge_base_id}/documents"]
    detail = paths["/api/v1/documents/{document_id}"]

    assert {"get", "post"}.issubset(collection)
    assert {"get"}.issubset(detail)
    assert collection["post"]["security"]
    assert detail["get"]["security"]


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
