"""定义可持久化评测样例、单次观测和最终报告的数据契约。"""

from enum import Enum
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


class RagExpectedBehavior(str, Enum):
    """描述资料是否足以支持回答，用于区分回答题和拒答题。"""

    ANSWER = "answer"
    REFUSE = "refuse"


class ExpectedEvidence(BaseModel):
    """用文件名和稳定原文片段描述一条标准证据。"""

    filename: str = Field(min_length=1, max_length=255)
    content_contains: str = Field(min_length=1, max_length=2000)

    @field_validator("filename", "content_contains", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        """在长度校验前清理空格，避免纯空白证据进入评测集。"""
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def identity(self) -> tuple[str, str]:
        """返回大小写不敏感的逻辑身份，用于识别重复标准证据。"""
        return (self.filename.casefold(), self.content_contains.casefold())


class RagEvaluationCase(BaseModel):
    """保存一个可重复执行的问题及其预期行为和标准证据。"""

    case_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    question: str = Field(min_length=1, max_length=4000)
    expected_behavior: RagExpectedBehavior
    expected_evidence: tuple[ExpectedEvidence, ...] = Field(default_factory=tuple)
    tags: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("case_id", "question", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        """统一清理样例标识与问题两端空格。"""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> object:
        """清理标签、移除空标签并保持首次出现顺序。"""
        if not isinstance(value, (list, tuple)):
            return value

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                normalized.append(item)
                continue
            tag = item.strip()
            key = tag.casefold()
            if tag and key not in seen:
                normalized.append(tag)
                seen.add(key)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_behavior_and_evidence(self) -> "RagEvaluationCase":
        """保证回答题有证据、拒答题无证据，并拒绝重复标准证据。"""
        if (
            self.expected_behavior == RagExpectedBehavior.ANSWER
            and not self.expected_evidence
        ):
            raise ValueError("可回答样例必须至少提供一条标准证据")
        if (
            self.expected_behavior == RagExpectedBehavior.REFUSE
            and self.expected_evidence
        ):
            raise ValueError("拒答样例不能配置标准证据")

        identities = [evidence.identity for evidence in self.expected_evidence]
        if len(identities) != len(set(identities)):
            raise ValueError("同一评测样例不能包含重复的标准证据")
        return self


class RetrievedEvidence(BaseModel):
    """保存一次检索返回给回答层的有序来源快照。"""

    source_id: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    score: float | None = Field(default=None, ge=-1.0, le=1.0)

    @field_validator("source_id", "filename", "content", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        """清理来源字段，防止空来源被误判为有效引用。"""
        if isinstance(value, str):
            return value.strip()
        return value


class RagEvaluationObservation(BaseModel):
    """记录某个样例的一次真实运行结果，供纯指标层消费。"""

    case_id: str = Field(min_length=1, max_length=120)
    retrieved_sources: tuple[RetrievedEvidence, ...] = Field(default_factory=tuple)
    refused: bool
    # None 表示本次只执行了检索，没有运行回答层；空元组表示回答层明确没有产生引用。
    cited_source_ids: tuple[str, ...] | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=255)
    similarity_threshold: float | None = Field(default=None, ge=-1.0, le=1.0)

    @field_validator("case_id", mode="before")
    @classmethod
    def normalize_case_id(cls, value: object) -> object:
        """清理关联标识，保证它能与评测样例稳定匹配。"""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("cited_source_ids", mode="before")
    @classmethod
    def normalize_citations(cls, value: object) -> object:
        """清理引用编号，保留重复值以便校验器明确报告数据错误。"""
        if value is None:
            return value
        if isinstance(value, (list, tuple)):
            return tuple(item.strip() if isinstance(item, str) else item for item in value)
        return value

    @model_validator(mode="after")
    def validate_source_identifiers(self) -> "RagEvaluationObservation":
        """来源和引用编号必须唯一，避免一个编号指向多条证据。"""
        source_ids = [source.source_id for source in self.retrieved_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("一次评测观测不能包含重复的 source_id")
        if self.cited_source_ids is not None and len(self.cited_source_ids) != len(
            set(self.cited_source_ids)
        ):
            raise ValueError("一次评测观测不能重复引用同一个 source_id")
        return self


class RagRetrievedSourceEvaluation(BaseModel):
    """保存不含原文的来源排名、分数和标准证据命中判断。"""

    rank: int = Field(ge=1)
    source_id: str
    filename: str
    score: float | None = Field(default=None, ge=-1.0, le=1.0)
    relevant: bool


class RagCaseEvaluationResult(BaseModel):
    """保存单个问题的指标明细，方便定位整体分数下降的原因。"""

    case_id: str
    recall_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    reciprocal_rank: float | None = Field(default=None, ge=0.0, le=1.0)
    # 旧基线使用 refusal_correct；读取时兼容旧字段，新报告统一输出更准确的名称。
    behavior_correct: bool = Field(
        validation_alias=AliasChoices("behavior_correct", "refusal_correct")
    )
    citation_valid: bool | None
    matched_evidence_count: int = Field(ge=0)
    expected_evidence_count: int = Field(ge=0)
    latency_ms: float | None = Field(default=None, ge=0.0)
    retrieved_count: int = Field(default=0, ge=0)
    top_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    retrieved_sources: tuple[RagRetrievedSourceEvaluation, ...] = Field(
        default_factory=tuple
    )
    tags: tuple[str, ...] = Field(default_factory=tuple)


class RagTagMetrics(BaseModel):
    """汇总同一业务场景标签下的检索和拒答表现。"""

    tag: str
    total_cases: int = Field(gt=0)
    answer_cases: int = Field(ge=0)
    refusal_cases: int = Field(ge=0)
    recall_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_reciprocal_rank: float | None = Field(default=None, ge=0.0, le=1.0)
    behavior_accuracy: float = Field(ge=0.0, le=1.0)
    refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)


class RagSelectorUsageSummary(BaseModel):
    """汇总证据选择模型的非敏感用量、延迟和可复算费用估计。"""

    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=255)
    thinking_enabled: bool
    calls: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    average_latency_ms: float | None = Field(default=None, ge=0.0)
    p95_latency_ms: float | None = Field(default=None, ge=0.0)
    input_price_per_million_usd: float = Field(ge=0.0)
    output_price_per_million_usd: float = Field(ge=0.0)
    estimated_cost_usd: float = Field(ge=0.0)
    # 当前 ChatResponse 不区分缓存命中 Token，因此按较保守的缓存未命中价格估算。
    pricing_basis: Literal["cache_miss"] = "cache_miss"


class RagEvaluationReport(BaseModel):
    """汇总整批评测的核心指标与逐题明细。"""

    embedding_model: str | None = None
    retrieval_strategy: str | None = None
    evidence_selector: str | None = None
    selector_usage: RagSelectorUsageSummary | None = None
    similarity_threshold: float | None = Field(default=None, ge=-1.0, le=1.0)
    top_k: int = Field(ge=1, le=20)
    total_cases: int = Field(gt=0)
    answer_cases: int = Field(ge=0)
    refusal_cases: int = Field(ge=0)
    recall_at_k: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_reciprocal_rank: float | None = Field(default=None, ge=0.0, le=1.0)
    # behavior_accuracy 负责全部样例；refusal_accuracy 只统计标准行为为拒答的样例。
    behavior_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_validity_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_latency_ms: float | None = Field(default=None, ge=0.0)
    p95_latency_ms: float | None = Field(default=None, ge=0.0)
    results: tuple[RagCaseEvaluationResult, ...]
    tag_metrics: tuple[RagTagMetrics, ...] = Field(default_factory=tuple)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_behavior_accuracy(cls, value: object) -> object:
        """旧报告曾把整体行为准确率写在 refusal_accuracy，读取时保留兼容。"""
        if isinstance(value, dict) and "behavior_accuracy" not in value:
            value = dict(value)
            value["behavior_accuracy"] = value.get("refusal_accuracy")
        return value
