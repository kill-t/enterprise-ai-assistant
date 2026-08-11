# RAG 检索评测 Runner 与报告输出

## 1. 本模块解决什么问题

上一模块已经能够根据人工提供的“评测样例”和“运行观测”计算指标，但还没有真的调用项目检索代码。

本模块把两边连接起来：

```text
JSONL 评测集
→ RagRetrievalEvaluationRunner
→ KnowledgeSearchService
→ Mock / 真实 Embedding
→ PostgreSQL / pgvector
→ RagMetricsEvaluator
→ JSON + Markdown 报告
```

现在更换 Embedding 模型、相似度阈值或 Top-K 后，可以对同一个知识库重复运行评测并比较结果。

## 2. 为什么复用 KnowledgeSearchService

Runner 没有重新写一套向量查询，而是调用在线接口背后的同一个 `KnowledgeSearchService`。

因此评测会自动复用：

- 用户所有权过滤。
- 知识库归档检查。
- Embedding 配置快照。
- pgvector 余弦检索。
- 相似度阈值。
- Top-K 和候选数量规则。
- Embedding 失败的统一错误边界。

如果评测直接访问 `document_chunks`，很可能绕过在线业务规则，最后测到的是另一套系统。

## 3. 完整调用链

命令行入口：

```text
python -m app.modules.knowledge.evaluation.cli
→ 解析 user_id、knowledge_base_id、top_k
→ RagEvaluationDatasetLoader.load_jsonl
→ SessionLocal 创建异步数据库 Session
→ KnowledgeSearchService
→ RagRetrievalEvaluationRunner.run
→ RagMetricsEvaluator.evaluate
→ RagEvaluationReportWriter.write
```

这里没有经过 FastAPI Router，因为评测是开发和 CI 工具，不是普通用户 HTTP 请求。
但它仍然显式传入 `user_id`，所以检索层的所有权规则没有被绕过。

## 4. Runner 如何处理单个问题

```text
记录开始时间
→ 构造 KnowledgeSearchRequest(question, top_k)
→ await KnowledgeSearchService.search(...)
→ 记录结束时间
→ 将 KnowledgeSearchHit 转为 RetrievedEvidence
→ results 为空时标记 refused=True
→ 生成 RagEvaluationObservation
```

Runner 只记录 `case_id`、来源快照和耗时，日志默认不输出完整问题和 Chunk 正文，避免企业资料进入普通日志。

## 5. 为什么使用 Protocol

Runner 依赖的是：

```python
class KnowledgeSearchProtocol(Protocol):
    async def search(...) -> KnowledgeSearchResponse:
        ...
```

这表示 Runner 只要求对象具有同样的 `search` 异步方法，而不要求它必须是某个具体类。

生产环境传入真实 `KnowledgeSearchService`；单元测试传入 `AsyncMock`。这样测试无需连接数据库，
同时生产调用仍使用真实业务实现。

替代方案是直接把类型写死为 `KnowledgeSearchService`。代码也能运行，但测试替身和后续检索策略适配会更生硬。

## 6. 为什么第一版串行运行

当前 Runner 按 JSONL 顺序一次执行一个问题：

```text
case-1 完成
→ case-2 完成
→ case-3 完成
```

优势：

- 延迟更容易解释。
- 不会因为同时请求过多导致本地数据库或模型限流。
- 失败时能准确知道停在哪一个 `case_id`。
- 报告顺序与数据集顺序一致，便于 Git 比较。

以后评测集扩大后，可以增加“有限并发”，但必须保留并发上限，不能无边界 `gather`。

## 7. 检索评测为什么不计算引用有效率

本模块只运行：

```text
问题 → 检索结果
```

没有运行：

```text
检索结果 → LLM 回答 → [S1] 引用
```

因此 Runner 将 `cited_source_ids` 设为 `None`，表示“没有执行引用评测”。报告显示：

```text
引用有效率：不适用
```

不能把所有检索结果自动当成已引用，否则引用有效率会永远是 100%，这个指标没有意义。

后续完整 Grounded Answer Runner 会真正检查：

- 回答是否引用来源。
- 引用编号是否存在。
- 引用原文能否支持对应事实。

## 8. 延迟如何计算

Runner 使用单调时钟 `time.perf_counter()`，只统计一次 `KnowledgeSearchService.search` 的耗时：

```text
latency_ms = (结束时间 - 开始时间) × 1000
```

报告包含：

- 平均检索延迟。
- P95 检索延迟。
- 每个问题的独立延迟。

P95 使用最近秩法。假设延迟已经从小到大排列，共有 20 个结果：

```text
rank = ceil(0.95 × 20) = 19
P95 = 第 19 个观测值
```

本地第一次查询可能因为数据库连接、模型初始化和缓存预热明显更慢，因此正式比较前应该执行预热，
并多次运行相同数据集。

## 9. JSON 与 Markdown 报告的分工

### JSON

适合：

- CI 自动读取。
- 比较两个版本的指标。
- 后续生成趋势图。
- 保存逐题机器数据。

### Markdown

适合：

- 开发者直接阅读。
- 代码审查。
- 面试演示。
- 快速定位失败样例。

两种报告来自同一个 `RagEvaluationReport`，避免各自重新计算后产生不一致。

## 10. Windows 下为什么不能直接使用普通 asyncio

命令行入口复用了项目已有的 `run_async`：

```text
Windows → SelectorEventLoop → psycopg 异步连接
其他系统 → asyncio.run
```

这是因为 Windows 默认 Proactor 事件循环与当前 psycopg 异步使用方式不兼容。评测 Runner 本身保持纯异步，
同步命令行入口负责选择正确事件循环。

## 11. 如何运行

在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m app.modules.knowledge.evaluation.cli `
  --dataset "evaluation\datasets\rag-v1.jsonl" `
  --user-id "用户 UUID" `
  --knowledge-base-id "知识库 UUID" `
  --top-k 5 `
  --output-directory "evaluation\reports"
```

参数解释：

- `--dataset`：JSONL 评测集。
- `--user-id`：知识库所属用户，用于真实权限过滤。
- `--knowledge-base-id`：本轮评测目标知识库。
- `--top-k`：每题最多返回多少个 Chunk，范围 1～20。
- `--output-directory`：报告目录。
- `--report-name`：可选报告名；不提供时使用 UTC 时间。

本地生成的 `evaluation/reports/` 已加入 `.gitignore`。报告可能包含 Chunk 内容或业务标识，
需要脱敏和人工确认后才能作为正式基线提交。

## 12. Mock 与真实 Embedding

Runner 根据知识库创建时冻结的 Embedding 快照选择 Provider：

```text
知识库快照 provider=mock
→ 不需要 API Key

知识库快照 provider=openai_compatible
→ 需要对应的 Embedding API Key
```

不能只修改 `.env` 就让已经入库的 Mock 知识库变成真实向量库。文档向量和查询向量必须属于同一个向量空间；
切换模型后需要新建知识库或执行后续的重新入库流程。

## 13. 失败如何处理

### 单题检索失败

```text
KnowledgeSearchService 抛出异常
→ 记录 case_id 和异常类型
→ RagEvaluationRunError
→ 整批评测失败
```

不会跳过失败题。否则只留下成功的简单问题，会得到虚假高分。

### 返回错误知识库

Runner 会校验 `response.knowledge_base_id` 与命令参数一致，防止测试替身或未来 Adapter 串库。

### 报告名称包含路径字符

报告名只允许字母、数字、下划线和连字符，阻止 `../` 等路径穿越写入。

## 14. 测试保护什么

- Runner 向真实检索契约传递用户、知识库、问题和 Top-K。
- 有结果时正常回答，无结果时标记拒答。
- Chunk UUID 转换为本次观测统一使用的字符串来源编号。
- 单题错误包含 `case_id`，但不泄露底层连接细节。
- 检索 Runner 的引用指标为 `None`。
- 平均延迟和 P95 由可控时钟稳定测试。
- JSON 保留中文且可被机器解析。
- Markdown 明确显示“不适用”而不是错误的 0%。

## 15. 本次真实冒烟结果如何理解

使用本地 Mock 知识库运行 3 个小样例：

```text
Recall@3：100%
MRR：75%
拒答准确率：100%
引用有效率：不适用
```

第二个问题的正确证据排在第 2 名，所以它的倒数排名是 `1/2=0.5`。另一个回答题为 `1.0`，
两题平均：

```text
MRR = (1.0 + 0.5) / 2 = 0.75
```

这说明 Mock 检索能够支持工程冒烟，但 3 个问题不能证明真实质量，下一步必须扩充人工评测集。

## 16. 面试表达

> 我把评测执行层和指标层分开。Runner 复用线上 KnowledgeSearchService，因此权限、Embedding 快照、相似度阈值和 pgvector 查询规则完全一致；指标层保持纯计算。检索评测不会伪造 LLM 引用，未执行的引用指标明确显示为不适用。报告同时输出 JSON 和 Markdown，并记录逐题延迟、平均延迟和 P95，便于在替换 Embedding 或增加重排序前后进行可重复比较。

## 17. 小练习

用 15～20 分钟增加一个测试：当检索 Service 返回的 `knowledge_base_id` 与 Runner 请求的不一致时，
应抛出包含当前 `case_id` 的 `RagEvaluationRunError`。

答案要点：

1. 构造请求知识库 UUID A。
2. Mock Service 返回知识库 UUID B。
3. 运行单个拒答样例。
4. 断言异常包含样例 ID。
5. 不需要连接数据库。

## 18. 下一步

下一模块建立第一批可维护的 RAG 数据集与语料：

```text
受控演示文档
→ 10～15 条种子问题
→ 回答题、拒答题、多证据题
→ Mock 基线报告
→ 再扩充到 30～50 条
```

这一步仍不需要真实 Embedding Key。种子数据稳定后，再向你确认真实 Embedding 平台和密钥，建立真正的语义检索基线。
