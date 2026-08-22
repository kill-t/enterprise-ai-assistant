"""定义可替换 OCR Provider，以及 RapidOCR 的本地 CPU 适配器。"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from typing import Any

from app.modules.knowledge.source_locator import NormalizedBoundingBox


@dataclass(frozen=True, slots=True)
class OcrImage:
    """保存送入 OCR 引擎的规范化 PNG 及其真实像素尺寸。"""

    content: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class OcrTextLine:
    """表示 OCR Provider 输出的一行文字、置信度和归一化区域。"""

    text: str
    confidence: float
    bounding_box: NormalizedBoundingBox


@dataclass(frozen=True, slots=True)
class OcrResult:
    """封装单张图片的有序 OCR 行和可审计引擎版本。"""

    lines: tuple[OcrTextLine, ...]
    engine: str


class OcrProviderError(Exception):
    """隐藏第三方 OCR 异常，交给 Parser 转换为稳定业务错误。"""


class BaseOcrProvider(ABC):
    """定义本地模型或未来云 OCR 都必须遵守的异步契约。"""

    @abstractmethod
    async def recognize(self, image: OcrImage) -> OcrResult:
        """识别一张已安全解码的图片，并返回统一行级结果。"""


class RapidOcrProvider(BaseOcrProvider):
    """使用 RapidOCR 与 ONNX Runtime 在本地 CPU 完成中英文识别。"""

    def __init__(
        self,
        *,
        engine_factory: Callable[[], Any] | None = None,
    ) -> None:
        """保存可注入引擎工厂；真实模型直到第一次调用才加载。"""
        self._engine_factory = engine_factory
        self._engine: Any | None = None
        self._engine_lock = Lock()
        try:
            package_version = version("rapidocr")
        except PackageNotFoundError:  # pragma: no cover - 依赖缺失由启动环境处理。
            package_version = "unknown"
        self.engine_name = f"rapidocr-{package_version}"

    async def recognize(self, image: OcrImage) -> OcrResult:
        """在线程池运行同步 ONNX 推理，避免阻塞 Worker 的异步数据库循环。"""
        try:
            output = await asyncio.to_thread(self._recognize_sync, image)
        except Exception as exc:
            raise OcrProviderError("OCR 引擎调用失败") from exc
        return OcrResult(lines=output, engine=self.engine_name)

    def _recognize_sync(self, image: OcrImage) -> tuple[OcrTextLine, ...]:
        """调用 RapidOCR，并把像素四边形统一成归一化矩形。"""
        engine = self._get_engine()
        result = engine(image.content)
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            boxes = ()

        lines: list[OcrTextLine] = []
        for text, score, box in zip(texts, scores, boxes, strict=False):
            normalized_text = str(text).strip()
            if not normalized_text:
                continue
            bounding_box = self._normalize_box(
                box,
                width=image.width,
                height=image.height,
            )
            if bounding_box is None:
                continue
            confidence = float(score)
            if not 0.0 <= confidence <= 1.0:
                continue
            lines.append(
                OcrTextLine(
                    text=normalized_text,
                    confidence=confidence,
                    bounding_box=bounding_box,
                )
            )
        return tuple(lines)

    def _get_engine(self) -> Any:
        """以双重检查方式只初始化一次模型，避免每个文档重复加载权重。"""
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                if self._engine_factory is not None:
                    self._engine = self._engine_factory()
                else:
                    from rapidocr import RapidOCR

                    self._engine = RapidOCR()
        return self._engine

    @staticmethod
    def _normalize_box(
        box: Any,
        *,
        width: int,
        height: int,
    ) -> NormalizedBoundingBox | None:
        """把 RapidOCR 四点像素坐标转为受 0～1 约束的外接矩形。"""
        try:
            points = list(box)
            x_values = [float(point[0]) for point in points]
            y_values = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            return None
        if not x_values or width <= 0 or height <= 0:
            return None

        x_min = max(0.0, min(1.0, min(x_values) / width))
        y_min = max(0.0, min(1.0, min(y_values) / height))
        x_max = max(0.0, min(1.0, max(x_values) / width))
        y_max = max(0.0, min(1.0, max(y_values) / height))
        if x_max <= x_min or y_max <= y_min:
            return None
        return NormalizedBoundingBox(
            x_min=x_min,
            y_min=y_min,
            x_max=x_max,
            y_max=y_max,
        )
