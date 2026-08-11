"""把同一份 RAG 评测报告输出为机器可读 JSON 和人工可读 Markdown。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.modules.knowledge.evaluation.models import RagEvaluationReport


@dataclass(frozen=True, slots=True)
class RagEvaluationArtifacts:
    """返回一次报告写入产生的两个文件路径。"""

    json_path: Path
    markdown_path: Path


class RagEvaluationReportWriter:
    """使用相同报告对象生成可比较数据和中文阅读报告。"""

    _SAFE_REPORT_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")

    def write(
        self,
        *,
        report: RagEvaluationReport,
        output_directory: str | Path,
        report_name: str,
    ) -> RagEvaluationArtifacts:
        """创建输出目录并写入 UTF-8 JSON 与 Markdown 文件。"""
        if not self._SAFE_REPORT_NAME.fullmatch(report_name):
            raise ValueError("评测报告名称只能包含字母、数字、下划线和连字符")

        directory = Path(output_directory)
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / f"{report_name}.json"
        markdown_path = directory / f"{report_name}.md"
        json_path.write_text(
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            self._render_markdown(report),
            encoding="utf-8",
        )
        return RagEvaluationArtifacts(
            json_path=json_path,
            markdown_path=markdown_path,
        )

    def _render_markdown(self, report: RagEvaluationReport) -> str:
        """渲染整体指标和逐题明细，不把未执行指标显示成零分。"""
        lines = [
            "# RAG 评测报告",
            "",
            "## 汇总指标",
            "",
            "| 指标 | 结果 |",
            "| --- | ---: |",
            f"| Embedding 模型 | {report.embedding_model or '不适用'} |",
            "| 相似度阈值 | "
            f"{self._format_number(report.similarity_threshold)} |",
            f"| 样例总数 | {report.total_cases} |",
            f"| 可回答样例 | {report.answer_cases} |",
            f"| 拒答样例 | {report.refusal_cases} |",
            f"| Recall@{report.top_k} | {self._format_rate(report.recall_at_k)} |",
            f"| MRR | {self._format_rate(report.mean_reciprocal_rank)} |",
            f"| 整体行为准确率 | {self._format_rate(report.behavior_accuracy)} |",
            f"| 拒答题准确率 | {self._format_rate(report.refusal_accuracy)} |",
            f"| 引用有效率 | {self._format_rate(report.citation_validity_rate)} |",
            f"| 平均检索延迟 | {self._format_latency(report.average_latency_ms)} |",
            f"| P95 检索延迟 | {self._format_latency(report.p95_latency_ms)} |",
            "",
            "## 逐题明细",
            "",
            "| case_id | Recall | RR | 召回数 | Top 分数 | 行为正确 | 引用有效 | 延迟 |",
            "| --- | ---: | ---: | ---: | ---: | :---: | :---: | ---: |",
        ]
        for result in report.results:
            lines.append(
                "| "
                f"{result.case_id} | "
                f"{self._format_rate(result.recall_at_k)} | "
                f"{self._format_rate(result.reciprocal_rank)} | "
                f"{result.retrieved_count} | "
                f"{self._format_number(result.top_score)} | "
                f"{self._format_bool(result.behavior_correct)} | "
                f"{self._format_bool(result.citation_valid)} | "
                f"{self._format_latency(result.latency_ms)} |"
            )
        lines.extend(
            [
                "",
                "## 按标签诊断",
                "",
                "| 标签 | 样例 | 答案题 | 拒答题 | Recall | MRR | 整体行为 | 拒答题准确率 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for metric in report.tag_metrics:
            lines.append(
                "| "
                f"{metric.tag} | "
                f"{metric.total_cases} | "
                f"{metric.answer_cases} | "
                f"{metric.refusal_cases} | "
                f"{self._format_rate(metric.recall_at_k)} | "
                f"{self._format_rate(metric.mean_reciprocal_rank)} | "
                f"{self._format_rate(metric.behavior_accuracy)} | "
                f"{self._format_rate(metric.refusal_accuracy)} |"
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_rate(value: float | None) -> str:
        """把 0 到 1 的指标显示为百分比，None 明确显示为不适用。"""
        return "不适用" if value is None else f"{value:.2%}"

    @staticmethod
    def _format_latency(value: float | None) -> str:
        """统一使用毫秒展示延迟。"""
        return "不适用" if value is None else f"{value:.2f} ms"

    @staticmethod
    def _format_bool(value: bool | None) -> str:
        """把布尔结果转换为容易扫描的中文状态。"""
        if value is None:
            return "不适用"
        return "是" if value else "否"

    @staticmethod
    def _format_number(value: float | None) -> str:
        """展示普通配置数值，避免把相似度阈值误格式化为百分比指标。"""
        return "不适用" if value is None else f"{value:g}"
