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
- `baselines/text-embedding-v4-1024-v1.json`
- `baselines/text-embedding-v4-1024-v1.md`

第二批困难评测资产：

- `corpus/challenge-v2/`：三份现行资料和一份历史干扰资料。
- `datasets/rag-v2-challenge.jsonl`：35 道改写、多文档、干扰和拒答问题。
- `baselines/rag-v2-challenge-comparison.json`
- `baselines/rag-v2-challenge-comparison.md`

困难集基线同时记录“整体行为准确率”和“拒答题准确率”。前者观察所有题是否正确选择回答或拒答，后者只观察标准拒答题，二者不能混为一个指标。

第三批多格式回归资产：

- `corpus/multiformat-v3/`：Markdown、TXT、文本 PDF、DOCX、XLSX、图片 OCR 和扫描 PDF OCR 七份固定语料。
- `datasets/rag-v3-multiformat.jsonl`：28 道可回答题和 7 道资料缺失拒答题。
- `baselines/rag-v3-multiformat-mock.json`：不含来源 UUID 和机器延迟的 Mock 向量质量基线。
- `baselines/rag-v3-multiformat-mock.md`：中文基线说明与逐格式指标。

多格式回归会调用正式 Parser、Token Chunker 和 Mock Embedding，但在进程内执行余弦排序，不依赖
PostgreSQL、MinIO 或 API Key。它验证“每种文件能否稳定变成可检索且可定位的 Chunk”，不替代在线环境的
用户隔离、任务恢复、pgvector 索引或真实语义模型评测。

在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m app.modules.knowledge.evaluation.offline_cli `
  --corpus-directory "evaluation\corpus\multiformat-v3" `
  --dataset "evaluation\datasets\rag-v3-multiformat.jsonl" `
  --top-k 3 `
  --similarity-threshold 0.2 `
  --output-directory "evaluation\reports" `
  --report-name "rag-v3-multiformat-mock-local"
```

本地报告保留来源诊断和真实运行延迟，因此只写到被 Git 忽略的 `reports/`。版本化 Mock 基线由维护者在
确认语料和指标变化后显式更新，不应在普通测试中自动覆盖。

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
