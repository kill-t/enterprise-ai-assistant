# 72. DeepSeek V4 Flash 证据选择真实评测

## 这一模块解决什么问题

上一模块已经实现 `LLMEvidenceSelector`，但 Mock 只能证明调用协议可用，不能证明模型真的会判断证据是否充分。

本模块使用同一份 35 题困难集进行真实对照：

```text
旧方案：text-embedding-v4 → 向量阈值 → Top-3

新方案：text-embedding-v4 → 宽召回 12 条候选
      → DeepSeek V4 Flash 判断证据充分性
      → 最终 Top-3 或拒答
```

只有新方案在召回、排序和拒答指标上产生净提升，才允许进入后续在线接入模块。

## 为什么使用 deepseek-v4-flash

评测执行时间为 2026-08-15。DeepSeek 官方已经提供：

- `deepseek-v4-flash`
- `deepseek-v4-pro`

旧模型名 `deepseek-chat` 已于 2026-07-24 停止使用，因此评测不能继续依赖本地旧配置名。官方资料：

- [DeepSeek 模型列表](https://api-docs.deepseek.com/api/list-models)
- [DeepSeek V4 发布说明](https://api-docs.deepseek.com/news/news260424/)
- [DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)

证据选择是一个短 JSON 分类任务，不需要长篇推理，所以本轮使用 Flash 非思考模式：

```text
model = deepseek-v4-flash
thinking = disabled
temperature = 0
response_format = json_object
```

非思考模式主要减少延迟和输出 Token。最终结构仍必须经过 Pydantic 校验，不能因为模型更强就跳过协议保护。

## 为什么不修改本机全局模型配置

本机 `.env` 的默认聊天 Provider 仍是 Mock，而且旧 DeepSeek 模型配置可能还用于其他开发场景。本模块没有直接改写 `.env`，而是沿 `ChatRequest.model` 显式传入评测模型：

```text
LLMEvidenceSelector(model_name="deepseek-v4-flash")
  ↓
ChatRequest.model = "deepseek-v4-flash"
  ↓
DeepSeekProvider 优先使用 request.model
  ↓
未指定 request.model 的普通聊天仍读取全局配置
```

这种请求级覆盖适合评测、灰度实验和不同任务选模，也避免一次实验意外改变整个系统。

## 为什么评测命令默认仍是免费的

CLI 设置了两道显式开关：

```text
默认：
--evidence-selector threshold

离线协议验证：
--evidence-selector llm --llm-provider mock

真实调用：
--evidence-selector llm --llm-provider deepseek
```

只写 `--evidence-selector llm` 仍会使用 Mock。必须额外指定 `--llm-provider deepseek` 才会创建真实 DeepSeek Provider，降低误运行整套付费评测的风险。

## 为什么 LLM 前关闭旧向量阈值

旧阈值 `0.45` 的作用是把低相似度候选直接删除。但当前实验要判断 LLM 是否能从宽候选中：

- 找回被固定阈值误删的正确证据。
- 去掉语义相关但没有答案的干扰证据。
- 对资料不存在的问题正确拒答。

因此本轮把候选阶段阈值设为 `-1.0`，先取 12 条候选，再让 LLM 最多选择 3 条。`-1.0` 是余弦相似度合法下界，表示本次实验不使用固定可靠性阈值。

这不是完全取消过滤。最终的严格过滤职责从固定数值转移给了 `LLMEvidenceSelector`。

## 真实运行中发现的协议问题

第一次完整运行在下面的多文档问题停止：

```text
不要混淆两个项目：蓝鲸与白海豚各由谁负责，附件存储方案又有什么不同？
```

模型返回的来源数量超过 Top-3。系统没有偷偷截断，因为直接取前三条可能丢失必要事实，却继续假装证据完整。

修复包含两部分：

1. System Prompt 明确规定 `selected_source_ids` 绝不能超过 `max_selected_sources`；如果数量上限内无法完整覆盖问题，必须返回 `incomplete_support`。
2. 只对结构化输出错误增加一次纠错重试；第二次仍不合法就继续抛出系统错误。

纠错重试不会把第一次异常输出重新发给模型，避免模型输出中意外包含候选正文。它只发送原始安全 Prompt 和错误类型对应的简短约束。

所有重试也会进入 Token、延迟和费用统计，不能成为隐藏成本。

## 为什么不直接截断模型结果

假设模型认为 S1、S2、S3、S4 合在一起才能回答，而上限是 3：

```text
错误做法：直接保留 S1、S2、S3
→ 可能缺少 S4 的关键否定条件
→ 下游仍收到 answerable=true

当前做法：拒绝第一次协议结果
→ 明确提醒最多选择 3 条
→ 3 条足够时重新选择最必要来源
→ 3 条不足时返回 incomplete_support
```

业务正确性比“尽量返回一个结果”更重要。

## 用量记录为什么不保存 Prompt

`LLMEvidenceCallMetrics` 只包含：

- 实际模型名。
- 输入、输出和总 Token。
- 单次模型延迟。

它不包含用户问题、候选 Chunk 或模型响应正文。评测报告能计算成本，但不会额外复制企业资料。

`LLMEvidenceUsageRecorder` 还会核对实际返回模型。如果请求配置为 Flash，API 却返回另一个模型，报告会失败，避免把质量和费用错误归因。

## 费用如何估算

当前内部 `ChatResponse` 只记录总输入 Token，没有区分缓存命中与未命中。报告因此按更保守的缓存未命中价格估算：

```text
估算费用
= 输入 Token × 输入单价 / 1,000,000
+ 输出 Token × 输出单价 / 1,000,000
```

本轮采用 2026-08-15 官方页面上的 Flash 单价：

```text
输入：$0.14 / 1M Token（缓存未命中）
输出：$0.28 / 1M Token
```

CLI 允许显式覆盖单价，因为模型价格会变化。版本化基线同时保存价格和 `pricing_basis=cache_miss`，以后可以复算，而不是只保存一个无法解释的金额。

## 真实评测结果

数据集：`rag-v2-challenge.jsonl`

- 总题数：35
- 可回答题：25
- 困难拒答题：10
- 最终 Top-K：3
- 候选上限：12

| 指标 | 旧真实向量阈值 | 向量宽召回 + Flash Selector | 变化 |
| --- | ---: | ---: | ---: |
| Recall@3 | 76.67% | 85.67% | +9.00 个百分点 |
| MRR | 79.33% | 92.00% | +12.67 个百分点 |
| 整体行为准确率 | 74.29% | 94.29% | +20.00 个百分点 |
| 可回答题准确率 | 100% | 92.00% | -8.00 个百分点 |
| 困难拒答准确率 | 10.00% | 100% | +90.00 个百分点 |
| 平均总延迟 | 2770.31 ms | 3722.91 ms | +952.60 ms |
| P95 总延迟 | 3972.21 ms | 4974.26 ms | +1002.05 ms |

这里的“旧可回答题准确率 100%”只表示旧阈值几乎总是返回候选，不代表返回的证据正确；它同时只有 10% 的困难拒答准确率。整体指标和证据命中指标必须一起看。

新方案 35 题中有 33 题行为正确：

- 10 道困难拒答全部正确拒绝。
- 25 道可回答题中 23 道正确放行。
- 两道错拒答为 `bw-acceptance-time`、`bw-auth-isolation-path`。

这两个 case_id 被保留在脱敏基线中，后续判断是宽召回未找到证据，还是 Selector 过于保守。不能为了把分数调满而立即针对测试题硬编码 Prompt。

## Selector 用量和延迟

| 项目 | 结果 |
| --- | ---: |
| 调用次数 | 35 |
| 输入 Token | 68,611 |
| 输出 Token | 927 |
| 总 Token | 69,538 |
| 平均 Selector 延迟 | 965.19 ms |
| P95 Selector 延迟 | 1192.54 ms |
| 35 题保守估价 | $0.0098651 |
| 平均每题估价 | 约 $0.000282 |

最终 35 次调用等于 35 道题，说明正式成功报告中没有发生纠错重试。第一次中断运行和单题诊断属于开发调试成本，不计入这份可比较成功基线。

## 当前架构决策

新方案在核心指标上产生了明显净提升，额外延迟约 1 秒、单题估算费用低于 0.001 美元。因此决策是：

```text
LLM Selector
→ 批准进入“可配置灰度接入”模块
→ 默认与阈值方案保持可切换
→ 无密钥或显式 Mock 环境继续使用阈值方案
→ Provider 故障不能伪装成无答案

Hybrid RRF
→ 仍不接线上
→ 等 LLM Selector 在线链路稳定后再单独组合评测
```

“评测通过”不等于立即删除旧实现。配置开关和可回退方案能让本地演示、CI、真实环境分别选择合适的成本与可靠性。

## 关键代码入口

- `backend/app/modules/knowledge/evaluation/cli.py`：安全开关、Selector 组装和真实评测命令。
- `backend/app/modules/knowledge/evaluation/selector_usage.py`：Token、延迟和费用汇总。
- `backend/app/modules/knowledge/evaluation/models.py`：报告中的 Selector 用量契约。
- `backend/app/modules/knowledge/retrieval/llm_evidence_selector.py`：结构校验、纠错重试和调用指标回调。
- `backend/app/infrastructure/llm/providers/deepseek_provider.py`：请求级模型覆盖和思考模式开关。
- `backend/evaluation/baselines/rag-v2-challenge-comparison.json`：不包含本地 UUID 和原文的版本化对比。

## 测试保护什么

- 默认 CLI 不会调用真实 LLM。
- 真实 Provider 必须显式选择。
- DeepSeek 使用请求指定的 Flash 模型。
- 非思考模式参数正确传给 OpenAI-compatible SDK。
- 模型超出来源数量时纠错一次，不能静默截断。
- 重试仍记录 Token 和延迟。
- 实际模型漂移会阻止生成错误归因报告。
- 报告正确显示用量、费用口径和 Selector 指标。
- 版本化基线不包含用户、知识库或 Chunk UUID。

## 面试时可以怎么讲

> 我没有因为接入 LLM Reranker 就直接上线，而是用同一份 35 题困难集与旧向量阈值做对照。向量先宽召回 12 条，DeepSeek V4 Flash 非思考模式再选择最多 3 条证据。Recall@3 从 76.67% 提升到 85.67%，MRR 提升到 92%，困难拒答从 10% 提升到 100%；代价是平均约 1 秒延迟和每题约 0.000282 美元。真实运行还发现模型会偶尔违反来源数量协议，所以我没有截断，而是进行一次受控纠错重试并统计额外成本。最终通过配置灰度接入，旧阈值方案保留为本地和回退路径。

## 15–30 分钟小练习

请用自己的话回答：

1. 为什么新方案“可回答题准确率”从 100% 降到 92%，整体质量反而更好？
2. 为什么模型多选一个来源时不能直接切掉最后一个？
3. 为什么报告要同时保存 Token 数、单价和费用口径，而不是只保存总金额？

答案要点：旧方案几乎不拒答但会放行错误证据；截断可能破坏证据完整性；价格会变化，只有保存用量和计算口径才能审计与复算。
