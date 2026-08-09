import asyncio
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

from app.core.config import ObjectStorageSettings
from app.infrastructure.object_storage.minio_storage import MinioObjectStorage


def test_object_storage_defaults_match_local_compose() -> None:
    """后端默认连接信息应与本地 Compose 的 MinIO 端口和账号一致。"""
    settings = ObjectStorageSettings()

    assert settings.endpoint == "localhost:9000"
    assert settings.access_key == "minioadmin"
    assert settings.secret_key.get_secret_value() == "minioadmin"
    assert settings.bucket_name == "enterprise-ai-documents"
    assert settings.max_file_size_bytes == 20 * 1024 * 1024


def test_minio_adapter_creates_bucket_only_once() -> None:
    """同一进程首次确认存储桶后，后续上传不应重复请求建桶。"""
    storage = build_minio_storage()
    storage.client.bucket_exists.return_value = False

    asyncio.run(storage.ensure_bucket())
    asyncio.run(storage.ensure_bucket())

    storage.client.bucket_exists.assert_called_once_with("enterprise-ai-documents")
    storage.client.make_bucket.assert_called_once_with("enterprise-ai-documents")


def test_minio_adapter_passes_stream_metadata_and_delete_key() -> None:
    """异步适配器应把对象键、长度、类型和文件流原样交给同步 SDK。"""
    storage = build_minio_storage()
    data = BytesIO(b"meeting notes")

    async def scenario() -> None:
        """依次调用异步上传和删除接口。"""
        await storage.put_object(
            object_key="users/u/documents/d.txt",
            data=data,
            length=13,
            content_type="text/plain",
        )
        await storage.delete_object("users/u/documents/d.txt")

    asyncio.run(scenario())

    storage.client.put_object.assert_called_once_with(
        bucket_name="enterprise-ai-documents",
        object_name="users/u/documents/d.txt",
        data=data,
        length=13,
        content_type="text/plain",
    )
    storage.client.remove_object.assert_called_once_with(
        bucket_name="enterprise-ai-documents",
        object_name="users/u/documents/d.txt",
    )


def test_compose_declares_visible_minio_container_and_named_volume() -> None:
    """Compose 应使用固定容器名和独立命名卷，便于 Docker Desktop 管理。"""
    compose_path = (
        Path(__file__).resolve().parents[2] / "infra" / "docker" / "docker-compose.yml"
    )
    compose_text = compose_path.read_text(encoding="utf-8")

    assert "container_name: enterprise-minio" in compose_text
    assert "minio_data:/data" in compose_text
    assert "minio_data:" in compose_text
    assert '"127.0.0.1:9000:9000"' in compose_text
    assert '"127.0.0.1:9001:9001"' in compose_text


def build_minio_storage() -> MinioObjectStorage:
    """构造不发起真实网络请求的 MinIO 适配器。"""
    storage = MinioObjectStorage(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False,
        bucket_name="enterprise-ai-documents",
    )
    storage.client = Mock()
    return storage
