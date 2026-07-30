from __future__ import annotations

from functools import lru_cache
from math import ceil
from typing import Literal, Protocol

from app.core.loggers import llm_logger
from app.infrastructure.llm.schemas import ChatMessage


class TokenEncoder(Protocol):
    """描述 tokenizer 最小能力，避免业务代码依赖某个具体实现类型。"""

    def encode(self, text: str) -> list[int]:
        """把文本编码为 token 编号序列。"""
        ...


class TokenCounter:
    """优先使用模型 tokenizer，并为未知模型提供保守估算。"""

    MESSAGE_OVERHEAD_TOKENS = 4
    REPLY_PRIMING_TOKENS = 3

    def __init__(
        self,
        model_name: str,
        encoder: TokenEncoder | Literal[False] | None = None,
    ) -> None:
        """初始化指定模型的计数器；False 专用于显式启用降级估算。"""
        self.model_name = model_name
        self._encoder = (
            self._load_model_encoder(model_name) if encoder is None else encoder
        )

    @property
    def uses_model_tokenizer(self) -> bool:
        """说明当前是否成功加载了模型专用 tokenizer。"""
        return self._encoder is not False

    def count(self, text: str) -> int:
        """统计文本 token；未知模型按每三个 UTF-8 字节一个 token 向上取整。"""
        if not text:
            return 0
        if self._encoder is not False:
            return len(self._encoder.encode(text))

        # 中文通常占三个 UTF-8 字节；该估算也会比英文常见的四字符一 token 更保守。
        return ceil(len(text.encode("utf-8")) / 3)

    def count_messages(self, messages: list[ChatMessage]) -> int:
        """估算 OpenAI-compatible 聊天消息及协议包装的总 token。"""
        if not messages:
            return 0

        message_tokens = sum(
            self.MESSAGE_OVERHEAD_TOKENS
            + self.count(message.role.value)
            + self.count(message.content)
            for message in messages
        )
        return message_tokens + self.REPLY_PRIMING_TOKENS

    @staticmethod
    @lru_cache(maxsize=32)
    def _load_model_encoder(model_name: str) -> TokenEncoder | bool:
        """加载已知模型编码器；依赖缺失或模型未知时安全降级。"""
        try:
            import tiktoken

            return tiktoken.encoding_for_model(model_name)
        except Exception as exc:
            # tiktoken 首次加载某些编码表时可能访问网络；离线失败不能阻断聊天。
            llm_logger.warning(
                "模型 tokenizer 不可用，改用保守估算 model=%s reason=%s",
                model_name,
                type(exc).__name__,
            )
            return False
