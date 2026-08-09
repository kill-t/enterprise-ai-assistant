from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """描述知识库冻结的 Provider、模型和向量维度。"""

    provider: str
    model: str
    dimension: int

    def __post_init__(self) -> None:
        """拒绝无法用于创建 Provider 的空配置或非正维度。"""
        if not self.provider.strip():
            raise ValueError("Embedding Provider 名称不能为空")
        if not self.model.strip():
            raise ValueError("Embedding 模型名称不能为空")
        if self.dimension <= 0:
            raise ValueError("Embedding 向量维度必须大于零")


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """表示一次保持输入顺序的批量文本向量化请求。"""

    texts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """表示一批有序向量及模型实际消耗的输入 Token。"""

    vectors: tuple[tuple[float, ...], ...]
    model: str
    dimension: int
    input_tokens: int = 0
