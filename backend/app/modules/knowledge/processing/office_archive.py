"""为 DOCX、XLSX 等 OOXML ZIP 容器提供共用的只读安全检查。"""

from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile, LargeZipFile, ZipFile


@dataclass(frozen=True, slots=True)
class OfficeArchiveLimits:
    """限制不可信 Office 压缩包解压时可消耗的资源。"""

    max_entries: int = 2_000
    max_uncompressed_bytes: int = 100 * 1024 * 1024
    max_entry_uncompressed_bytes: int = 50 * 1024 * 1024
    max_compression_ratio: int = 500

    def __post_init__(self) -> None:
        """所有资源上限必须为正数，避免配置本身关闭安全保护。"""
        if any(
            limit <= 0
            for limit in (
                self.max_entries,
                self.max_uncompressed_bytes,
                self.max_entry_uncompressed_bytes,
                self.max_compression_ratio,
            )
        ):
            raise ValueError("Office 压缩包资源上限必须大于零")


class OfficeArchiveValidationError(Exception):
    """区分损坏容器与通过结构伪装的危险容器。"""

    def __init__(self, reason: str, *, unsafe: bool) -> None:
        """保存可由具体 Parser 转换为稳定业务错误的信息。"""
        self.reason = reason
        self.unsafe = unsafe
        super().__init__(reason)


def validate_office_archive(
    content: bytes,
    *,
    required_parts: frozenset[str],
    limits: OfficeArchiveLimits,
    forbidden_prefixes: tuple[str, ...] = (),
) -> None:
    """检查必要 OOXML 部件、宏、外链目录、加密标记和解压资源上限。"""
    normalized_forbidden_prefixes = tuple(
        prefix.replace("\\", "/").lower() for prefix in forbidden_prefixes
    )
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if not required_parts.issubset(names):
                raise OfficeArchiveValidationError(
                    "缺少必要的 OOXML 部件",
                    unsafe=False,
                )
            if len(entries) > limits.max_entries:
                raise OfficeArchiveValidationError(
                    "压缩包条目数量超过限制",
                    unsafe=True,
                )

            total_uncompressed = 0
            for entry in entries:
                normalized_name = entry.filename.replace("\\", "/").lower()
                if entry.flag_bits & 0x1:
                    raise OfficeArchiveValidationError(
                        "不支持加密的 Office 压缩包",
                        unsafe=True,
                    )
                if normalized_name.endswith("vbaproject.bin"):
                    raise OfficeArchiveValidationError(
                        "文件中包含宏载荷",
                        unsafe=True,
                    )
                if any(
                    normalized_name.startswith(prefix)
                    for prefix in normalized_forbidden_prefixes
                ):
                    raise OfficeArchiveValidationError(
                        "文件中包含当前不允许的外部链接部件",
                        unsafe=True,
                    )
                if entry.file_size > limits.max_entry_uncompressed_bytes:
                    raise OfficeArchiveValidationError(
                        "单个压缩条目解压后过大",
                        unsafe=True,
                    )

                total_uncompressed += entry.file_size
                if total_uncompressed > limits.max_uncompressed_bytes:
                    raise OfficeArchiveValidationError(
                        "压缩包解压后总大小超过限制",
                        unsafe=True,
                    )
                if _compression_ratio(entry.file_size, entry.compress_size) > (
                    limits.max_compression_ratio
                ):
                    raise OfficeArchiveValidationError(
                        "压缩率异常，疑似压缩炸弹",
                        unsafe=True,
                    )

            # 宏启用内容类型不应该出现在第一版只允许的 .docx 或 .xlsx 中。
            content_types = archive.read("[Content_Types].xml").lower()
            if b"macroenabled" in content_types:
                raise OfficeArchiveValidationError(
                    "文件声明为宏启用文档",
                    unsafe=True,
                )
    except OfficeArchiveValidationError:
        raise
    except (
        BadZipFile,
        LargeZipFile,
        KeyError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise OfficeArchiveValidationError(
            "压缩包损坏或格式无效",
            unsafe=False,
        ) from exc


def _compression_ratio(uncompressed_size: int, compressed_size: int) -> float:
    """空条目比例为零；非空但压缩后为零视为无限压缩率。"""
    if uncompressed_size == 0:
        return 0.0
    if compressed_size == 0:
        return float("inf")
    return uncompressed_size / compressed_size
