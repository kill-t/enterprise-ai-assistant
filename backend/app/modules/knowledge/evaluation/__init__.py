"""RAG 离线评测的数据契约、数据集加载和指标计算能力。"""

from app.modules.knowledge.evaluation.dataset_loader import (
    RagEvaluationDatasetError,
    RagEvaluationDatasetLoader,
)
from app.modules.knowledge.evaluation.metrics import (
    RagEvaluationError,
    RagMetricsEvaluator,
)
from app.modules.knowledge.evaluation.models import (
    ExpectedDocumentSourceLocator,
    ExpectedEvidence,
    ExpectedOcrRegionSourceLocator,
    ExpectedPageSourceLocator,
    ExpectedSectionSourceLocator,
    ExpectedSheetRangeSourceLocator,
    RagEvaluationCase,
    RagEvaluationObservation,
    RagEvaluationReport,
    RagExpectedBehavior,
    RagSelectorUsageSummary,
    RetrievedEvidence,
)
from app.modules.knowledge.evaluation.offline_corpus import (
    OfflineCorpusError,
    OfflineCorpusSearchService,
    sanitize_report_for_baseline,
)
from app.modules.knowledge.evaluation.report_writer import (
    RagEvaluationArtifacts,
    RagEvaluationReportWriter,
)
from app.modules.knowledge.evaluation.runner import (
    RagEvaluationRunError,
    RagRetrievalEvaluationRunner,
)
from app.modules.knowledge.evaluation.selector_usage import (
    LLMEvidenceUsageRecorder,
    RagSelectorUsageError,
)

__all__ = [
    "ExpectedEvidence",
    "ExpectedDocumentSourceLocator",
    "ExpectedOcrRegionSourceLocator",
    "ExpectedPageSourceLocator",
    "ExpectedSectionSourceLocator",
    "ExpectedSheetRangeSourceLocator",
    "RagEvaluationCase",
    "RagEvaluationDatasetError",
    "RagEvaluationDatasetLoader",
    "RagEvaluationError",
    "RagEvaluationObservation",
    "RagEvaluationReport",
    "RagEvaluationArtifacts",
    "RagEvaluationReportWriter",
    "RagEvaluationRunError",
    "RagExpectedBehavior",
    "RagSelectorUsageError",
    "RagSelectorUsageSummary",
    "RagMetricsEvaluator",
    "RagRetrievalEvaluationRunner",
    "LLMEvidenceUsageRecorder",
    "OfflineCorpusError",
    "OfflineCorpusSearchService",
    "RetrievedEvidence",
    "sanitize_report_for_baseline",
]
