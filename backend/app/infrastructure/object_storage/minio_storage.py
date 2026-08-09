import asyncio
import threading
from functools import partial
from typing import BinaryIO

from minio import Minio

from app.infrastructure.object_storage.base import BaseObjectStorage
from app.infrastructure.object_storage.errors import (
    ObjectStorageReadLimitExceededError,
)


class MinioObjectStorage(BaseObjectStorage):
    """将同步 MinIO Python SDK 适配成不阻塞事件循环的异步接口。"""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket_name: str,
    ) -> None:
        """根据受控配置创建 MinIO Client，并保存私有存储桶名称。"""
        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self.bucket_name = bucket_name
        self._bucket_ready = False
        # 同步 SDK 会在线程池执行，因此使用线程锁保护首次建桶竞争。
        self._bucket_lock = threading.Lock()

    async def ensure_bucket(self) -> None:
        """在线程池中检查并按需创建存储桶。"""
        if self._bucket_ready:
            return
        await asyncio.to_thread(self._ensure_bucket_sync)

    def _ensure_bucket_sync(self) -> None:
        """串行执行首次建桶，避免并发请求重复创建同一个桶。"""
        with self._bucket_lock:
            if self._bucket_ready:
                return
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
            self._bucket_ready = True

    async def put_object(
        self,
        object_key: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> None:
        """在线程池中上传文件流，避免同步网络 I/O 阻塞 FastAPI。"""
        upload = partial(
            self.client.put_object,
            bucket_name=self.bucket_name,
            object_name=object_key,
            data=data,
            length=length,
            content_type=content_type,
        )
        await asyncio.to_thread(upload)

    async def delete_object(self, object_key: str) -> None:
        """在线程池中删除对象，供跨基础设施失败补偿使用。"""
        delete = partial(
            self.client.remove_object,
            bucket_name=self.bucket_name,
            object_name=object_key,
        )
        await asyncio.to_thread(delete)

    async def get_object(self, object_key: str, max_bytes: int) -> bytes:
        """在线程池下载受限大小对象，并可靠归还底层 HTTP 连接。"""
        if max_bytes <= 0:
            raise ValueError("对象下载大小上限必须大于零")
        return await asyncio.to_thread(
            self._get_object_sync,
            object_key,
            max_bytes,
        )

    def _get_object_sync(self, object_key: str, max_bytes: int) -> bytes:
        """读取上限外的一个字节以识别对象是否被替换成超大内容。"""
        response = self.client.get_object(
            bucket_name=self.bucket_name,
            object_name=object_key,
        )
        try:
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ObjectStorageReadLimitExceededError("远端对象大小超过文档记录")
            return content
        finally:
            # MinIO 官方示例要求同时 close 与 release_conn，避免连接池泄漏。
            response.close()
            response.release_conn()
