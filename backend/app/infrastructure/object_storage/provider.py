from functools import lru_cache

from app.core.config import settings
from app.infrastructure.object_storage.base import BaseObjectStorage
from app.infrastructure.object_storage.minio_storage import MinioObjectStorage


@lru_cache
def get_object_storage() -> BaseObjectStorage:
    """创建并复用线程安全的 MinIO 对象存储适配器。"""
    storage_settings = settings.object_storage
    return MinioObjectStorage(
        endpoint=storage_settings.endpoint,
        access_key=storage_settings.access_key,
        secret_key=storage_settings.secret_key.get_secret_value(),
        secure=storage_settings.secure,
        bucket_name=storage_settings.bucket_name,
    )
