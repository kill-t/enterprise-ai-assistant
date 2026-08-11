"""从便于 Git 审查的 JSONL 文件加载 RAG 评测样例。"""

import json
from pathlib import Path

from pydantic import ValidationError

from app.modules.knowledge.evaluation.models import RagEvaluationCase


class RagEvaluationDatasetError(ValueError):
    """表示评测文件不可读取、格式错误或样例标识冲突。"""


class RagEvaluationDatasetLoader:
    """逐行解析 JSONL，并在进入评测前完成结构校验。"""

    def load_jsonl(self, path: str | Path) -> tuple[RagEvaluationCase, ...]:
        """加载非空 JSON 行，并把解析错误包装成带行号的中文信息。"""
        dataset_path = Path(path)
        try:
            lines = dataset_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RagEvaluationDatasetError(
                f"无法读取 RAG 评测文件：{dataset_path}"
            ) from exc

        cases: list[RagEvaluationCase] = []
        seen_case_ids: set[str] = set()
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            case = self._parse_line(line=line, line_number=line_number)
            if case.case_id in seen_case_ids:
                raise RagEvaluationDatasetError(
                    f"RAG 评测文件第 {line_number} 行包含重复的 case_id："
                    f"{case.case_id}"
                )
            seen_case_ids.add(case.case_id)
            cases.append(case)

        if not cases:
            raise RagEvaluationDatasetError("RAG 评测文件至少需要一个有效样例")
        return tuple(cases)

    @staticmethod
    def _parse_line(*, line: str, line_number: int) -> RagEvaluationCase:
        """解析单行 JSON，并避免在错误信息中泄露整条评测正文。"""
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RagEvaluationDatasetError(
                f"RAG 评测文件第 {line_number} 行不是有效 JSON"
            ) from exc

        try:
            return RagEvaluationCase.model_validate(payload)
        except ValidationError as exc:
            raise RagEvaluationDatasetError(
                f"RAG 评测文件第 {line_number} 行不符合样例契约：{exc}"
            ) from exc
