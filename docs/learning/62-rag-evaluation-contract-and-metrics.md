# RAG 评测契约与离线指标

## 1. 本模块解决什么问题

当前 RAG 已经能够完成：

```text
上传文件
→ 文档入库
→ 向量检索
→ 根据证据回答或拒答
→ 保存并展示引用
```

但“功能能运行”不等于“检索质量可靠”。如果以后把 Mock Embedding 换成真实模型，
或者增加混合检索、重排序、MQE、HyDE，只看几个手工问题很容易得到主观结论。

本模块建立一个与在线业务解耦的离线评测底座：

```text
标准评测样例
→ 一次运行观测
→ 纯指标计算
→ 逐题明细与汇总报告
```

本模块不连接数据库、不调用 Embedding、不调用 LLM，因此不需要 API Key。

## 2. 整体数据流

```text
JSONL 文件
→ RagEvaluationDatasetLoader
→ RagEvaluationCase

现有检索 Service（下一模块接入）
→ RagEvaluationObservation

RagEvaluationCase + RagEvaluationObservation
→ RagMetricsEvaluator
→ RagEvaluationReport
```

各部分职责：

- `RagEvaluationCase`：描述问题、预期回答行为和标准证据。
- `ExpectedEvidence`：用文件名和稳定原文片段标识正确证据。
- `RagEvaluationObservation`：记录检索结果、是否拒答和引用编号。
- `RagMetricsEvaluator`：只负责公式，不访问任何基础设施。
- `RagEvaluationReport`：同时保存整体分数和逐题明细。

## 3. 为什么标准证据不使用 Chunk UUID

数据库中的 Chunk 使用 UUID 很适合业务关联，但不适合作为长期评测标识。

例如重新入库同一个文件后：

```text
原 Chunk UUID：A
重新入库后的 Chunk UUID：B
```

文件里的事实没有变化，但 UUID 已经变化。如果评测集只保存 UUID，所有标准答案都会失效。

当前方案使用：

```json
{
  "filename": "project.md",
  "content_contains": "项目负责人是林晓"
}
```

当检索结果满足以下两个条件时，认为标准证据被召回：

1. 文件名一致。
2. Chunk 正文包含标准原文片段。

这个方案比 UUID 稳定，也比只比较文件名严格。它的缺点是文件正文被改写后需要更新评测集，
但这正是评测数据应该被人工审查的变化。

## 4. JSONL 是什么

JSONL 表示“一行一个 JSON 对象”：

```json
{"case_id":"meeting-owner","question":"负责人是谁？","expected_behavior":"answer","expected_evidence":[{"filename":"project.md","content_contains":"负责人是林晓"}]}
{"case_id":"unknown-budget","question":"量子燃料配额是多少？","expected_behavior":"refuse"}
```

相比一个巨大的 JSON 数组，JSONL 的优势是：

- Git 中每条样例更容易审查。
- 增加一个问题通常只增加一行。
- 后续可以逐行处理大型评测集。
- 单行格式错误能精确报告行号。

加载器会拒绝：

- 无效 JSON。
- 空数据集。
- 重复 `case_id`。
- 可回答问题没有标准证据。
- 拒答问题却配置了标准证据。

## 5. 四个核心指标

### 5.1 Recall@K

Recall@K 表示前 K 条结果召回了多少标准证据：

```text
Recall@K = Top-K 命中的标准证据数 / 标准证据总数
```

一个问题有两条标准证据，Top-2 只找到一条：

```text
Recall@2 = 1 / 2 = 0.5
```

最终报告对所有可回答问题做宏平均，避免证据很多的问题支配整体分数。

### 5.2 MRR

MRR 关注第一条相关证据出现得有多早：

```text
第 1 名相关 → 1 / 1 = 1.0
第 2 名相关 → 1 / 2 = 0.5
第 4 名相关 → 1 / 4 = 0.25
没有相关结果 → 0
```

再对所有可回答问题取平均值。

Recall@K 与 MRR 不能互相替代：

- Recall 高、MRR 低：正确资料找到了，但排序太靠后。
- MRR 高、Recall 低：第一条结果很好，但多证据问题召回不完整。

### 5.3 拒答准确率

每个样例都声明预期行为：

- `answer`：资料足够，应正常回答。
- `refuse`：资料不足，应明确拒答。

只要系统实际行为与预期相同，该题就记为正确。最终计算全部样例的正确比例。

### 5.4 引用有效率

当前采用严格的逐题规则：

- 正常回答必须至少引用一个本次检索到的真实 `source_id`。
- 所有引用编号都必须属于本次检索结果。
- 拒答不能携带引用。

满足规则的样例数除以总样例数，得到引用有效率。后续还会增加“引用覆盖率”，
检查回答是否引用了支持关键事实的正确证据，而不仅仅是编号存在。

## 6. 真实调用链中的位置

本模块目前只完成绿色部分的右半段：

```text
评测数据集
→ 【下一模块】调用 KnowledgeSearchService
→ 【下一模块】转换 RetrievedEvidence
→ RagEvaluationObservation
→ 【本模块】RagMetricsEvaluator
→ JSON / Markdown 报告
```

指标层不直接调用 `KnowledgeSearchService`，原因是职责不同：

- Runner 负责执行系统并收集实际结果。
- Evaluator 负责比较实际结果与标准答案。

如果两者混在一起，测试指标公式时就必须准备数据库和向量模型，调试成本会明显增加。

## 7. 关键代码入口

- `backend/app/modules/knowledge/evaluation/models.py`
  - 评测样例、标准证据、观测、逐题结果和汇总报告。
- `backend/app/modules/knowledge/evaluation/dataset_loader.py`
  - JSONL 加载、行号定位和重复 ID 检查。
- `backend/app/modules/knowledge/evaluation/metrics.py`
  - Recall@K、MRR、拒答准确率和引用有效率。
- `backend/tests/test_rag_evaluation_foundation.py`
  - 使用人工可计算的小数据保护指标公式。

## 8. 正常、失败和边界流程

### 正常流程

```text
读取 JSONL
→ Pydantic 校验每一行
→ case_id 一一关联观测
→ 按原样例顺序计算
→ 返回报告
```

### JSON 格式错误

```text
json.loads 失败
→ 包装为 RagEvaluationDatasetError
→ 中文信息指出具体行号
→ 不泄露整行评测正文
```

### 漏跑问题

```text
评测集有 case A
→ 实际观测没有 case A
→ 立即失败
```

不能把漏跑的问题默认为 0 分或直接忽略，否则可能因为只执行了简单问题而得到虚假高分。

### 全部为拒答题

Recall 和 MRR 对拒答题没有定义，因此报告返回 `null`，而不是伪造为 0 或 1。

## 9. 测试如何保护行为

- 契约测试：回答题必须有证据，拒答题必须无证据。
- 加载测试：空行可忽略，错误 JSON 精确定位到行。
- 唯一性测试：重复 `case_id` 被拒绝。
- 公式测试：人工计算的 Recall@2 和 MRR 与程序一致。
- 完整性测试：缺少或多出观测时评测失败。

测试驱动时第一次执行出现：

```text
ModuleNotFoundError: No module named 'app.modules.knowledge.evaluation'
```

这说明测试已经表达了预期入口，但实现尚不存在。实现完成后同一组测试变为 5 个全部通过，
这就是“先定义行为，再补实现”的闭环。

## 10. 替代方案与取舍

### 使用 RAGAS、DeepEval 等现成框架

优点：指标丰富，能够快速接入 LLM Judge。

缺点：第一步就引入较重依赖，会掩盖 Recall、MRR 和拒答规则的基础原理；部分指标还依赖付费模型。

当前先实现少量透明指标。后续需要语义正确性、忠实度和 LLM Judge 时，再通过 Adapter 接入第三方评测框架。

### 只保存标准答案文本

优点：数据集容易编写。

缺点：无法区分“没检索到资料”和“模型拿到资料却回答错误”。当前同时保存标准证据，
能够先单独评估检索层。

## 11. 常见错误

| 错误 | 后果 | 当前保护 |
| --- | --- | --- |
| 用 Chunk UUID 作为长期标准答案 | 重新入库后评测集失效 | 文件名 + 原文片段 |
| 漏跑问题后直接忽略 | 指标虚高 | 样例与观测必须一一对应 |
| 把拒答题加入 Recall 平均 | 指标含义错误 | 拒答题的 Recall/MRR 为 null |
| 只检查引用编号格式 | 可能引用不存在的来源 | 编号必须属于本次检索结果 |
| 只看平均分 | 不知道具体失败问题 | 报告保存逐题明细 |

## 12. 面试表达

可以这样介绍：

> 我没有把 RAG 验收限制为接口返回 200，而是建立了独立的 JSONL 评测契约。标准证据使用文件名和稳定原文片段，避免重新入库后 Chunk UUID 变化导致数据集失效。指标层与检索执行层解耦，先透明实现 Recall@K、MRR、拒答准确率和引用有效率，并强制检查漏跑样例。这样后续替换 Embedding、增加混合检索或重排序时，可以在同一数据集上比较质量、延迟和成本。

## 13. 小练习

建议用 15～20 分钟完成：

为 `test_rag_evaluation_foundation.py` 增加一个测试，验证同一个样例配置两条完全相同的
`ExpectedEvidence` 时，Pydantic 会拒绝它。

答案要点：

1. 构造 `expected_behavior="answer"` 的样例。
2. `expected_evidence` 放入两条相同的文件名和原文片段。
3. 使用 `pytest.raises(ValidationError, match="重复的标准证据")`。
4. 不需要修改生产代码，因为唯一性规则已经实现。

## 14. 下一步

下一模块实现可执行 Runner：

```text
读取 JSONL
→ 选择目标知识库
→ 调用现有 KnowledgeSearchService
→ 转换成 RagEvaluationObservation
→ 输出 JSON 报告
```

Runner 第一版继续支持 Mock Embedding，不需要密钥。真正比较语义检索质量时，再配置真实
Embedding API Key，并在同一评测集上保存基线结果。
