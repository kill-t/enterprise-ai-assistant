# RAG 评测资产

本目录保存可公开、可复现且经过人工审查的 RAG 评测资产：

```text
evaluation/
├─ corpus/       受控演示语料
├─ datasets/     JSONL 问题与标准证据
├─ baselines/    已确认的版本化基线
└─ reports/      本地临时报告，已被 Git 忽略
```

第一批资产：

- `corpus/enterprise-ai-rag-demo.md`
- `datasets/rag-v1-seed.jsonl`
- `baselines/mock-hash-1024-v1.json`
- `baselines/mock-hash-1024-v1.md`

运行前应把受控语料上传到目标知识库并等待文档状态变为 `READY`。然后在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m app.modules.knowledge.evaluation.cli `
  --dataset "evaluation\datasets\rag-v1-seed.jsonl" `
  --user-id "用户 UUID" `
  --knowledge-base-id "知识库 UUID" `
  --top-k 3 `
  --output-directory "evaluation\reports"
```

本地报告可能包含业务标识或检索来源，不应直接提交。只有完成脱敏、核对模型与阈值后，才能人工保存到 `baselines/`。
