# RAG 种子评测集与 Mock 基线

## 1. 本模块解决什么问题

评测 Runner 已经能够执行任意 JSONL，但如果每个人临时上传不同文档、随手写几个问题，
运行结果仍然无法复现。

本模块把“考试资料、考试题和第一次成绩”一起版本化：

```text
受控语料 corpus
→ 人工标注数据集 datasets
→ 真实检索 Runner
→ 已审查基线 baselines
```

这样修改分块、Embedding、阈值或排序算法后，可以在同一套资料上重新考试。

## 2. 评测目录结构

```text
backend/evaluation/
├─ corpus/
│  └─ enterprise-ai-rag-demo.md
├─ datasets/
│  └─ rag-v1-seed.jsonl
├─ baselines/
│  ├─ mock-hash-1024-v1.json
│  └─ mock-hash-1024-v1.md
└─ reports/
   └─ 本地临时报告，不进入 Git
```

- `corpus`：允许公开和重复上传的受控文档。
- `datasets`：问题、预期行为、标准证据和标签。
- `baselines`：经过人工确认、允许提交的历史结果。
- `reports`：每次本地运行的原始结果，可能包含业务信息，默认忽略。

## 3. 种子集如何组成

第一批共有 14 个问题：

```text
10 个可回答问题
├─ 单事实题
├─ 多证据题
└─ 系统边界题

4 个必须拒答的问题
├─ 青铜铸造
├─ 海洋生物
├─ 气候地理
└─ 虚构航天
```

为什么不全部写成容易命中的问题：

- 单事实题检查基础召回。
- 多证据题检查一个问题需要多个事实时是否召回完整。
- 系统边界题覆盖存储、Redis、OCR 和失败恢复等面试重点。
- 拒答题检查相似度阈值是否会把无关 Chunk 当作证据。

拒答题来自不同领域，避免只用同一种无关问法得到偶然的高分。

## 4. 语料与数据集如何防止漂移

测试会读取真实 Markdown，并检查：

```text
每个 answer 样例
→ 每条 ExpectedEvidence.filename 等于受控文件名
→ 每条 content_contains 确实存在于受控正文
```

如果有人把“负责人是林晓”改成“负责人是王宁”，却没有同步修改数据集，测试会立即失败。

同时还检查：

- 总题数为 14。
- 回答题为 10，拒答题为 4。
- 至少包含一个多证据题。
- 问题不能重复。
- 四个拒答题具有不同领域标签。
- 基线结果的 case_id 顺序与数据集完全一致。

## 5. 为什么报告必须记录模型和阈值

下面两份报告不能直接比较：

```text
报告 A：Recall@3 = 90%
报告 B：Recall@3 = 95%
```

如果不知道它们分别使用什么 Embedding 和相似度阈值，分数没有完整含义。

现在报告增加：

```json
{
  "embedding_model": "mock-hash-1024",
  "similarity_threshold": 0.2,
  "top_k": 3
}
```

指标层还会拒绝把多个模型或多个阈值的观测混进同一报告。

## 6. 为什么模型配置来自检索响应

Runner 没有直接读取当前 `.env` 后假定模型，而是记录 `KnowledgeSearchService` 的真实响应：

```text
知识库创建时冻结 Embedding 快照
→ 检索按知识库快照选择 Provider
→ SearchResponse 返回实际模型与阈值
→ Runner 写入 Observation
→ Evaluator 写入 Report
```

这很重要，因为修改 `.env` 不会自动改变已经入库的向量。报告应该描述真正参与检索的配置，
而不是描述开发者以为自己配置的值。

## 7. 本次 Mock 基线结果

```text
Embedding 模型：mock-hash-1024
相似度阈值：0.2
Top-K：3
样例：14
Recall@3：100%
MRR：90%
拒答准确率：100%
引用有效率：不适用
```

`meeting-schedule` 和 `meeting-goal` 的正确证据排在第 2 名：

```text
RR = 1 / 2 = 0.5
```

其余 8 个回答题第一名命中：

```text
MRR = (8 × 1.0 + 2 × 0.5) / 10 = 0.9
```

所以“全部召回”不代表“排序全部最佳”。这两个问题将成为后续真实 Embedding 和重排序实验的观察点。

## 8. 为什么 100% Recall 仍不能证明 RAG 很好

当前有明显限制：

- 只有一份短 Markdown。
- 只有 14 个问题。
- 问题和原文词语比较接近。
- Mock Embedding 主要依赖词和字符哈希，不理解真正语义。
- 还没有近义改写、跨文档问题和干扰文档。
- 没有运行最终回答，因此没有忠实度和引用覆盖率。

当前基线的正确定位是“可复现工程基线”，不是“生产质量证明”。

## 9. 延迟数据如何理解

首次查询包含数据库连接和 Provider 初始化，明显慢于后续查询。因此本次 P95 被首题冷启动影响。

延迟比较必须满足：

- 同一台机器或相近运行环境。
- 相同数据集和 Top-K。
- 明确是否预热。
- 多次运行并保存样本量。

不能拿本地 Mock 的 20ms 与远程真实 Embedding 的结果直接比较后断言算法更快。

## 10. 基线为什么同时保存 JSON 和 Markdown

JSON 用于程序读取和未来自动比较；Markdown 用于人工审查与面试展示。

基线不是每次运行都自动提交，因为原始报告可能来自真实企业知识库。正确流程是：

```text
生成 reports 临时结果
→ 检查是否包含敏感内容
→ 核对模型、阈值、Top-K 和样例版本
→ 人工脱敏
→ 保存到 baselines
```

## 11. 如何重新运行

1. 创建一个使用 Mock Embedding 的知识库。
2. 上传 `backend/evaluation/corpus/enterprise-ai-rag-demo.md`。
3. 等待状态变为 `READY`。
4. 在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m app.modules.knowledge.evaluation.cli `
  --dataset "evaluation\datasets\rag-v1-seed.jsonl" `
  --user-id "用户 UUID" `
  --knowledge-base-id "知识库 UUID" `
  --top-k 3 `
  --output-directory "evaluation\reports" `
  --report-name "mock-seed-local"
```

## 12. 关键代码和资产入口

- `backend/evaluation/corpus/enterprise-ai-rag-demo.md`
- `backend/evaluation/datasets/rag-v1-seed.jsonl`
- `backend/evaluation/baselines/mock-hash-1024-v1.json`
- `backend/evaluation/baselines/mock-hash-1024-v1.md`
- `backend/tests/test_rag_evaluation_seed_dataset.py`
- `backend/app/modules/knowledge/evaluation/metrics.py`
- `backend/app/modules/knowledge/evaluation/report_writer.py`

## 13. 常见错误

| 错误 | 后果 | 当前保护 |
| --- | --- | --- |
| 修改语料但不改标准证据 | 数据集失真 | CI 检查证据片段存在 |
| 只写回答题 | 无法评估误召回 | 固定包含 4 个拒答领域 |
| 报告不记录模型 | 无法复现和比较 | 报告包含模型与阈值 |
| 把多个阈值混成平均分 | 指标失去含义 | Evaluator 直接拒绝 |
| 自动提交所有本地报告 | 泄露业务片段 | reports 默认 Git 忽略 |
| 把 Mock 100% 当成生产质量 | 结论错误 | 文档明确工程边界 |

## 14. 面试表达

> 我把受控语料、JSONL 标注集和基线报告一起版本化，并用测试保证标准证据确实存在于语料、基线 case_id 与数据集完全一致。第一批包含单事实、多证据、系统边界和跨领域拒答题。报告记录真实检索响应中的 Embedding 模型、相似度阈值和 Top-K，指标层禁止混合配置。Mock 基线用于验证工程可复现性，真实语义质量会在相同数据集上用真实 Embedding 再测。

## 15. 小练习

用 15～30 分钟新增一条“验收待办”问题：

```text
蓝鲸项目验收前还需要完成哪些测试？
```

要求：

1. 标记为 `answer`。
2. 标准证据必须直接复制受控语料中的原文片段。
3. 添加“多证据”或“验收”标签。
4. 把测试中的总样例数和回答题数量各加 1。
5. 运行种子数据测试，确认语料与标注没有漂移。

答案要点：不要先看模型能不能搜到再决定标准答案；标准答案应由语料事实和业务期望人工确定。

## 16. 下一步

下一阶段开始建立真实语义基线：

```text
确认真实 Embedding 平台和 API Key
→ 创建 openai_compatible 知识库
→ 使用 text-embedding-v4 重新入库受控语料
→ 运行同一份 14 题数据集
→ 比较 Mock 与真实模型
→ 再扩展到 30～50 题和多文档干扰集
```

这一步开始需要真实 Embedding API Key，但不需要聊天模型 Key。
