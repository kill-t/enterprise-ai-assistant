# 摘要批次 Token 上限与调用前预算检查

## 1. 本模块解决什么问题

滚动摘要已经能够生成，但如果 Redis 或 Celery Worker 停止较长时间，PostgreSQL 可能积累大量尚未摘要的消息。

错误做法：

```text
积累 30000 token 旧消息
→ 创建一个覆盖全部旧消息的任务
→ 把系统提示词、旧摘要和 30000 token 一次交给模型
→ 超过模型上下文，任务失败
```

本模块把旧历史拆成可连续推进的摘要批次，并在真正调用模型前进行第二次总预算检查。

## 2. 两层预算分别负责什么

系统现在有两个不同的摘要预算。

### 第一层：批次软上限

配置：

```dotenv
CONVERSATION_MEMORY__SUMMARY_BATCH_TOKENS=4000
```

它只控制“本次任务最多选择多少旧消息”，不包含系统提示词和旧摘要。

```text
所有可摘要旧轮次
→ 从最旧轮次开始
→ 按完整轮次累计 token
→ 接近 4000 token 时停止
→ 任务目标冻结到本批最后一个助手消息
```

### 第二层：模型输入硬上限

模型真正可用的输入预算：

```text
可用输入 token
= 模型上下文长度
- 最大输出 token
- 安全余量
```

调用前统计完整 Prompt：

```text
系统摘要指令
+ 旧滚动摘要
+ 本批新增消息
```

只有总输入不超过硬上限时才调用 Provider。

## 3. 为什么需要两层，而不是只用一个数字

批次选择发生在聊天请求结束后的调度阶段。当时只需要快速确定任务目标，适合使用稳定的批次配置。

完整 Prompt 要到 Worker 读取旧摘要后才能确定：

```text
同样是 4000 token 新消息
旧摘要 500 token → 可能放得下
旧摘要 3000 token → 可能放不下
```

所以调度阶段做粗粒度限制，模型调用前做最终裁决。

这和机场行李检查类似：预订时有行李额度，真正托运时仍要称重。

## 4. 为什么必须从最旧消息开始

假设尚未摘要的历史是：

```text
第 1 轮 → 第 2 轮 → 第 3 轮 → 第 4 轮
```

如果预算只能容纳两轮，本批必须选择：

```text
第 1 轮 + 第 2 轮
```

不能直接选择第 3、4 轮，否则摘要游标推进后，第 1、2 轮会被错误视为已经覆盖，形成永久遗漏。

因此批次永远是连续旧前缀，摘要游标只能向前连续移动。

## 5. 为什么不能截断半个轮次

一轮可能是：

```text
user：询问会议风险
assistant：解释风险和负责人
```

如果批次只保留用户问题，不保留助手回答，摘要会得到不完整事实。

选择算法以完整轮次为最小单位：

- 以 `user` 开始。
- 以 `assistant` 结束。
- 将来中间可以包含 Tool 消息。
- 整轮加入或整轮不加入。

## 6. 单个轮次就超过软上限怎么办

`SUMMARY_BATCH_TOKENS` 是软上限。第一轮本身超过 4000 token 时，系统仍然完整选择这一轮。

原因是另外两种行为更差：

- 截断：破坏完整轮次和原始语义。
- 永远不选：摘要游标永久卡住。

但这不表示一定会调用模型。Processor 仍会执行硬预算检查：

```text
单轮虽超过批次软上限
→ 构造完整 Prompt
→ 如果超过模型硬上限
→ 不调用 Provider，任务失败且游标不移动
```

以后可增加“超大单轮分块压缩”能力，但必须单独设计如何保留消息边界和引用关系。

## 7. 模型输入预算来自哪里

每个 Provider 已有：

- `context_window_tokens`
- `max_output_tokens`
- `token_safety_margin`

`ModelContextSettings.available_input_tokens` 统一计算输入上限。前台聊天和后台摘要使用相同公式，避免两个模块各自计算出不同结果。

`ProviderFactory.get_model_name()` 返回默认 Provider 实际使用的模型名，使摘要预检能选择对应 tokenizer；未知模型仍使用项目已有的保守估算。

## 8. 调用前检查在哪里发生

`RollingConversationSummaryProcessor.generate()` 执行顺序：

```text
构造 ChatRequest
→ TokenCounter.count_messages(request.messages)
→ 与 input_budget_tokens 比较
→ 超限：抛出 ConversationSummaryPromptBudgetExceededError
→ 未超限：provider.chat(request)
```

检查发生在 `provider.chat` 之前，因此确定超限的请求不会产生 API 调用、费用或无意义等待。

Worker 会沿用已有失败流程：

```text
回滚摘要半成品
→ 记录任务 FAILED
→ 原始消息和旧摘要保持不变
```

## 9. 配置如何传递

```text
backend/.env
→ Settings.conversation_memory.summary_batch_tokens
→ ConversationService
→ ConversationSummaryJobService
→ 选择任务冻结目标
```

模型硬预算：

```text
ProviderFactory.get_context_settings
→ ModelContextSettings.available_input_tokens
→ Celery Task 组装 RollingConversationSummaryProcessor
→ generate 调用前检查
```

新增配置已经同步到 `backend/.env.example`。本模块没有修改数据库结构，不需要 Alembic 迁移。

## 10. 真实调用示例

假设：

```text
摘要触发阈值：3000
最近原文预算：2000
摘要批次预算：4000
未摘要历史：10000 token
```

可能得到：

```text
最近约 2000 token：保留原文
更早约 8000 token：允许进入摘要
本次只冻结最早约 4000 token
剩余约 4000 token：等待后续批次
```

Worker 还会把旧摘要和系统提示词算入硬预算。如果完整 Prompt 放不下，本次不会调用模型。

## 11. 测试保护什么

- 积压历史只冻结最早的一个批次。
- 最近完整轮次继续保留原文。
- 批次不能截断半个问答。
- 单个超大轮次仍能作为完整软上限例外。
- 完整 Prompt 超过硬预算时不调用 Provider。
- 模型输入预算统一扣除输出预留和安全余量。
- 批次配置必须为正数且默认值为 4000。
- 新配置已同步到 `.env.example`。

## 12. 常见错误

- 只限制新消息，没有计算旧摘要和系统 Prompt。
- 从最新旧消息开始摘要，跳过游标前面的历史。
- 为严格满足数字而截断半个问答轮次。
- 预算超限后仍然调用模型，依赖厂商返回错误。
- 使用 `len(text)` 代替模型 tokenizer 或保守估算。
- 新增配置后忘记更新环境变量模板。

## 13. 面试表达

可以这样说明：

> 滚动摘要使用两层 token 防护。调度阶段用 summary_batch_tokens 从最旧历史开始选择连续完整轮次，防止 Worker 积压后一次发送全部消息；单个超大轮次作为软上限例外以保证游标能推进。Worker 构造完整 Prompt 后，再按模型上下文减去输出预留和安全余量进行硬检查，统计系统词、旧摘要和本批消息，超限时不会调用 Provider，也不会推进摘要游标。

## 14. 小练习

假设可摘要旧轮次分别占用：

```text
第 1 轮：1500 token
第 2 轮：1800 token
第 3 轮：1200 token
批次软上限：4000 token
```

请回答：

1. 本批应该选择哪几轮？
2. 为什么不能再加入第 3 轮？
3. 选择完成后，是否可以直接确定模型一定能接收？

答案要点：选择第 1、2 轮，共 3300 token；加入第 3 轮会达到 4500，超过批次软上限；还不能确定，因为调用前必须把系统提示词和旧摘要一起计算硬预算。
