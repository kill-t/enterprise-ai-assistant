# 模块 82：多格式 RAG 回归与来源位置准确率

## 1. 这个模块解决什么业务问题

前面已经分别实现 PDF、Markdown、TXT、DOCX、XLSX、图片 OCR 和扫描 PDF OCR，但“每个 Parser 的单元
测试通过”还不能证明完整 RAG 链路可靠。例如：

- PDF 正文被正确提取，但答案引用成了错误页码。
- Excel 能读到数据，但工作表或单元格范围在检索转换时丢失。
- OCR 能识别文字，但 `source_type` 被错误保存为普通文本。
- 文件格式全部可解析，但查询向量没有召回对应 Chunk。

本模块建立一套可提交仓库、无需外部服务的固定多格式语料，并增加“来源位置准确率”。它回答两个不同
问题：

```text
Recall@K：正确正文有没有进入前 K 条结果？
来源位置准确率：命中的正文是否仍指向正确文件类型、页码、章节、单元格或 OCR 区域？
```

两者必须分开。正文相同但页码错误时，Recall 仍是 1，来源位置准确率必须是 0；否则系统会生成看似
正确、实际无法回查的引用。

## 2. 在整体架构中的位置

在线 RAG 链路是：

```text
MinIO 原文件
→ Parser
→ TokenAwareChunker
→ Embedding Provider
→ PostgreSQL / pgvector
→ Search Service
→ Grounded Answer / SSE / Citation
```

多格式离线回归复用中间的正式实现，只替换两端基础设施：

```text
仓库内七份固定文件
→ 正式 DocumentParserRegistry
→ 正式 TokenAwareChunker
→ 正式 MockEmbeddingProvider
→ 进程内余弦排序
→ 正式 RagRetrievalEvaluationRunner
→ RagMetricsEvaluator
→ JSON / 中文 Markdown 报告
```

这样可以在没有 PostgreSQL、MinIO、Celery 和 API Key 时快速发现格式回归。它不负责验证用户权限、
数据库索引、任务恢复或真实语义质量，这些仍由集成测试和真实 Embedding 基线负责。

## 3. 为什么固定语料包含七种格式

`backend/evaluation/corpus/multiformat-v3/` 包含：

| 文件 | 验证重点 |
| --- | --- |
| `atlas-overview.md` | Markdown 标题路径 |
| `borealis-sla.txt` | 无结构纯文本整篇位置 |
| `cetus-policy.pdf` | 两页文本 PDF 的准确页码 |
| `draco-meeting.docx` | Word 标题、正文、列表和表格块 |
| `equuleus-budget.xlsx` | 工作表、数据范围和表头范围 |
| `fornax-scan.png` | 独立图片 OCR、置信度和区域 |
| `gemini-scan.pdf` | 图片型 PDF 的 OCR 回退与原页码 |

这些资料都是虚构且不含真实个人数据。DOCX、XLSX 和 PDF 在生成后进行了视觉渲染检查；随后又经过
项目自己的 Parser 和 Chunker 解析，避免“看起来正常但程序读不到”。

## 4. 数据集怎样描述标准位置

旧评测证据只有文件名和原文片段：

```json
{
  "filename": "cetus-policy.pdf",
  "content_contains": "供应商证明材料保存期限为七年"
}
```

现在可以额外声明来源类型和格式特有位置：

```json
{
  "filename": "cetus-policy.pdf",
  "content_contains": "供应商证明材料保存期限为七年",
  "source_type": "pdf",
  "source_locator": {
    "kind": "page",
    "page_start": 2,
    "page_end": 2,
    "heading_path": []
  }
}
```

五类期望位置分别是：

- `document`：TXT 等整篇来源。
- `page`：PDF 连续页码。
- `section`：Markdown、DOCX 的标题路径与可选内容块。
- `sheet_range`：XLSX 工作表、数据范围和表头范围。
- `ocr_region`：OCR 页码、最低置信度和引擎前缀。

OCR 的精确坐标不参与版本化断言。模型升级、渲染 DPI 或检测框微小变化都可能改变坐标，但页码、最低
置信度和引擎身份应保持可审计。运行时的真实归一化坐标仍会进入引用快照。

## 5. 指标怎样计算

对一个可回答问题，先在 Top-K 中按“文件名 + 原文片段”匹配标准证据：

```text
matched_evidence_count / expected_evidence_count = Recall@K
```

然后只对配置了位置期望的证据继续检查 Locator：

```text
matched_source_location_count / expected_source_location_count
= 来源位置准确率
```

逐题结果会保存：

- 召回率与首条相关结果排名。
- 期望和实际命中的位置数量。
- 每条已检索来源的位置是否正确。
- 来源类型、分数、行为正确性和延迟。

报告再按题目做宏平均，并按 `Markdown`、`DOCX`、`XLSX` 等标签分别诊断。旧数据集没有位置期望时，
位置指标显示“不适用”，不会被错误计算为零分，所以旧基线保持兼容。

## 6. 离线搜索为什么只允许 Mock Embedding

`OfflineCorpusSearchService` 明确拒绝真实 Provider。原因不是项目不支持真实 Embedding，而是这套基线的
目的在于：

- CI 或新开发者无需密钥即可运行。
- 不产生调用费用。
- 同一文本始终生成相同哈希向量。
- Parser 或 Locator 改动能被快速定位，不与外部模型版本变化混在一起。

Mock 仍经过正式 `EmbeddingProviderFactory`，不是测试中硬编码排名。查询向量和 Chunk 向量用余弦相似度
排序，再转换成正式 `KnowledgeSearchHit`，所以 Runner 后面的数据流与在线检索相同。

它不是真实语义模型。本次 Recall@3 为 85.71%，文本 PDF 只有 50%，正好说明“哈希重叠”无法稳定理解
“达到多少钱”和“复核阈值”这类改写。不能为了让测试全绿而偷偷把问题改成原文复制；后续应使用真实
Embedding 基线比较语义提升。

## 7. 这次真实发现的 Excel 问题

多格式语料第一次经过 `XlsxParser` 时，有效工作簿被报告为损坏。原因是 OOXML 允许不写工作表
`dimension`，openpyxl 只读模式此时会返回：

```python
formula_sheet.max_row is None
formula_sheet.max_column is None
```

旧代码直接执行 `None > 50000`，触发 `TypeError`，随后被统一转换成“Excel 文件损坏”。修复后的分层
保护是：

```text
dimension 存在 → 进入解析前快速检查行列上限
dimension 缺失 → 跳过快速检查
实际遍历时    → 每一行、每一列、非空单元格总数继续强制限流
```

这不是放松安全边界，而是把“可选元数据”与“真实资源计数”区分开。多格式端到端回归比只用 openpyxl
自己生成测试文件更容易发现不同 OOXML 生产工具之间的兼容差异。

## 8. 基线为什么要脱敏

本地完整报告包含：

- 稳定或真实的 Chunk ID。
- 逐条来源诊断。
- 当前机器的检索延迟。

这些数据有的可能关联业务资源，有的会随电脑性能波动。`sanitize_report_for_baseline()` 在提交版本前删除：

- `retrieved_sources` 明细和来源 ID。
- 每题延迟、平均延迟和 P95 延迟。

质量指标、Top 分数、标签结果和位置准确率仍被保留。完整报告只进入被 Git 忽略的
`backend/evaluation/reports/`，用于本机调试。

## 9. 关键代码入口

### `evaluation/models.py`

- `ExpectedEvidence`：标准原文、来源类型和期望 Locator。
- `Expected*SourceLocator`：五类版本化位置断言。
- `RetrievedEvidence`：Runner 收到的真实来源与 Locator。
- `RagEvaluationReport`：增加整体、逐题和按标签位置指标。

### `evaluation/metrics.py`

- `_matches_content()`：只判断文件和原文，维持 Recall 的原定义。
- `_matches_source_location()`：按 Locator 类型比较稳定字段。
- `_source_location_valid_for_result()`：生成逐来源诊断。
- `_calculate_tag_metrics()`：按格式查看位置是否在某一条链路中丢失。

### `evaluation/offline_corpus.py`

- `build()`：真实解析、分块、批量 Mock Embedding 和稳定 ID。
- `search()`：进程内余弦排序并还原正式 Search Response。
- `sanitize_report_for_baseline()`：生成可提交的确定性质量基线。

### `evaluation/offline_cli.py`

负责加载 JSONL、运行 35 道问题、写本地双格式报告，并可由维护者显式输出脱敏基线。

## 10. 测试分别保护什么

- 正文命中时如果 PDF 页码错误：Recall 为 1，来源位置准确率为 0。
- Runner 必须把 `source_type/source_locator` 从检索响应完整传入评测层。
- 数据集固定为 35 题、28 道回答题、7 道拒答题和七种格式。
- 每条标准证据都能在真实 Parser/Chunker 输出中找到，且位置完全匹配。
- OCR 语料用真实 RapidOCR 执行，不用假数据绕过格式链路。
- 离线搜索确实使用正式 Parser、Chunker 和 Mock Provider。
- 空语料或未知格式立即失败，不能静默缩小评测分母。
- 版本化基线不保存 UUID、来源明细和机器延迟。
- artifact-tool 生成、没有 `dimension` 的有效 XLSX 能被正式 Parser 读取。

## 11. 当前基线结果如何理解

| 指标 | Mock 结果 |
| --- | ---: |
| Recall@3 | 85.71% |
| MRR | 85.71% |
| 整体行为准确率 | 88.57% |
| 拒答题准确率 | 100.00% |
| 来源位置准确率 | 85.71% |

另外有一个“格式真实性检查”：评测代码直接把每道标准证据对应文件的全部真实 Chunk 作为观测，七类格式
的正文和位置准确率均为 100%。它证明标准答案与 Parser 输出一致。85.71% 是 Mock 检索未召回四道题，
不是 Parser 丢失了正文。

延迟只记录在本地报告，不提交版本化基线。OCR 模型初始化和整批建索引时间不计入单题检索延迟；如果
要做性能基准，应单独记录冷启动、文档解析、Embedding 和查询四段时间。

## 12. 面试时可以怎么表达

> 我没有把“RAG 回答看起来对”当作唯一验收。我建立了七种文件格式的固定语料和 35 道评测题，复用
> 正式 Parser、Chunker 与 Embedding 契约运行离线回归。除了 Recall@K 和 MRR，我把引用位置单独建模，
> 能检测正文正确但页码、Word 内容块、Excel 单元格或 OCR 区域错误的问题。基线会移除业务 UUID 和
> 机器延迟。这个回归还发现并修复了缺少 OOXML dimension 时的 Excel 兼容问题。

## 13. 小练习（15～30 分钟）

在 `rag-v3-multiformat.jsonl` 中增加一道关于 Word “记录人江越”的可回答题，要求：

1. 标准证据使用 `draco-meeting.docx`。
2. `source_type` 为 `docx`。
3. Locator 指向“天龙计划季度会 → 角色分工”的列表块。
4. 先只运行数据集与真实 Parser 测试，再运行离线 Mock 评测。
5. 解释为什么新增题可能改变 Recall，却不应改变 Parser 位置真实性检查。

答案要点：该证据和“主持人赵岚”位于同一个列表 Chunk，`block_kind=list`、`block_index=0`；Parser 真实性
测试会直接提供对应文件的真实 Chunk，所以只要标准答案正确就应命中。Mock 检索是否把该 Chunk 排进
Top 3 取决于查询与 Chunk 的哈希特征，属于另一个阶段。

## 14. 下一模块

RAG 第一阶段到这里完成收口：文件入库、OCR、混合检索、证据选择、引用展示和多格式评测都有可复现
证据。下一阶段进入 MCP：先建立统一 Tool Definition、MCP Server 配置与风险模型，再实现只读工具发现
和本地办公沙箱。RAG 的真实多格式 Embedding 对照、重排序、OCR 版面分析和 Office 图片文字识别保留为
后续数据驱动增强项，不在没有评测收益前继续堆算法。
