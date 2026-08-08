# Service 流式消息状态与事务边界

## 1. 本模块解决什么问题

Provider 已经能产生：

```text
delta → delta → completed / error
```

但 Provider 不知道当前用户是谁，也不负责数据库消息、Redis 缓存和摘要任务。Conversation Service 必须把“模型数据流”转换成“可靠的聊天业务流”。

本模块增加：

```python
async for event in service.stream_message(...):
    ...
```

本模块先完成 Python 内部异步生成器；它现在已经由第 24 模块包装成 HTTP SSE，Service 自身仍然不依赖 HTTP。

## 2. Service 为什么不能直接转发 Provider 事件

Provider 的 delta 只有正文：

```json
{"type": "delta", "delta": "会议"}
```

前端和业务层还需要知道：

- 这段正文属于哪条助手消息。
- 用户问题是否已经保存。
- 助手消息当前是 PENDING、COMPLETED、FAILED 还是 CANCELLED。
- 最终正文是否已经提交数据库。
- 失败后应该用哪条消息 ID 重试。

所以 Service 定义自己的四种事件：

```text
started
delta
completed
error
```

Provider 事件是模型协议；Service 事件是聊天业务协议。两层名字相似，但职责不同。

## 3. 完整调用链

```text
ConversationService.stream_message
→ 校验用户拥有该会话
→ 读取摘要与历史
→ 构建 ChatRequest
→ 同一事务创建 user + pending assistant
→ commit
→ 尽力把 user 写入 Redis
→ yield started
→ async for 消费 Provider 事件
   ├─ delta：内存拼接并 yield delta
   ├─ completed：校验后保存完整助手消息
   └─ error：保存 FAILED 与部分正文
→ 成功时回填 Redis 并尽力调度摘要任务
```

## 4. started 事件表示什么

`started` 携带两条已经持久化的消息：

```text
user_message.status = COMPLETED
assistant_message.status = PENDING
```

它不是“模型已经开始返回文字”，而是“数据库已经接受这次生成任务”。

后续每个 delta 都携带相同的 `assistant_message.id`。将来前端可以先创建稳定的助手消息气泡，再把片段追加到该气泡，而不是每个片段创建一条新消息。

## 5. 为什么 user 和 assistant 占位要在同一事务创建

旧流程分两次提交：

```text
创建 user
→ commit
→ 创建 assistant 占位
→ commit
```

如果第二次数据库写入失败，会留下一个没有对应助手状态的孤立问题。

现在改为：

```text
BEGIN
→ 创建 user
→ 创建 pending assistant
→ COMMIT
```

任何一条创建失败：

```text
ROLLBACK
→ 两条都不保存
→ 不调用模型
```

它们共同表达“系统接受了一次回答生成”，因此属于同一数据库事务。

## 6. 为什么模型失败不回滚用户消息

模型调用发生在消息对已经提交之后：

```text
消息对 commit
→ 调用模型
```

这是有意设计的。模型超时不代表用户没有问过问题，也不应该删除审计事实。模型失败时系统保存：

```text
user = COMPLETED
assistant = FAILED
```

用户随后可以使用相同助手消息 ID 原位重试。

这与“创建消息对时数据库失败”不同：

- 创建阶段数据库失败：整个消息对回滚。
- 模型阶段失败：保留用户问题，把助手消息标记失败。

## 7. 为什么 delta 不实时写 PostgreSQL

假设模型产生 500 个片段，如果每个片段都执行 UPDATE：

```text
500 个 delta
→ 500 次数据库写入和事务提交
```

这会制造写放大、锁竞争和大量无价值的中间版本。

当前策略：

```text
delta 到达
→ 追加到 content_parts 内存列表
→ 产生 Service delta
→ 不更新消息表
```

正常完成后只执行一次：

```text
assistant.content = 完整正文
assistant.status = COMPLETED
→ UPDATE
→ COMMIT
```

因此，流式改善的是用户等待体验，不应该自动变成高频数据库写入。

## 8. completed 成功终点做什么

Service 收到 Provider completed 后先检查：

```text
所有 delta 拼接正文
==
completed.response.content
```

一致时才保存：

- 完整正文。
- 实际模型名称。
- prompt、completion 和 total token。
- `status = COMPLETED`。
- `finished_at`。

数据库提交成功后：

```text
尽力写 Redis
→ 尽力创建滚动摘要任务
→ yield Service completed
```

所以收到 Service completed，表示完整助手消息已经成功持久化，而不是只表示模型停止输出。

## 9. error 失败终点做什么

Provider error 或未预期异常都会使助手消息进入：

```text
status = FAILED
error_code = 稳定错误码
error_message = 安全中文提示
finished_at = 当前时间
```

如果失败前已经输出部分 delta，本模块会在失败终点把部分正文保存一次：

```text
content = 用户已经看到的部分回答
status = FAILED
```

失败消息不会写入 Redis，也不会参与模型历史和滚动摘要，因为历史查询只读取 `COMPLETED`。

重试时仍复用原助手消息 ID，并在开始重试时清空旧的部分正文和错误信息。

## 10. 什么是流协议错误

Service 不只处理网络异常，还验证 Provider 是否遵守内部协议。

以下情况记录为：

```text
MODEL_STREAM_PROTOCOL_ERROR
```

### 没有终点

```text
delta("未完成")
→ Provider 静默结束
```

没有 completed，就不能证明模型正常完成。

### 正文不一致

```text
delta 拼接 = "用户已经看到的正文"
completed.content = "另一段正文"
```

如果直接保存 completed，数据库内容会与用户实际收到的内容不同。因此这次回答必须失败，并保留已经发送的部分正文用于排查。

## 11. 为什么显式关闭 Provider 生成器

Service 读取到 completed 或 error 后已经得到终态，不会继续请求下一项。但 Provider 生成器可能仍持有底层 HTTP 流。

当前使用：

```python
async with aclosing(provider.stream_chat(request)) as provider_stream:
    async for event in provider_stream:
        ...
```

离开代码块时会调用 `aclose()`，使 Provider 的 `finally` 立即执行并释放网络资源，而不是等待 Python 以后回收对象。

## 12. 普通聊天为什么也进行了小幅重构

非流式 `send_message` 和流式 `stream_message` 现在复用两个步骤：

```text
_prepare_message_generation
→ 所有权、历史、Prompt、消息对事务

_complete_assistant_response
→ 完整正文、终态提交、Redis、摘要调度
```

这避免两条路径以后出现不同的事务规则。普通聊天仍使用：

```python
await provider.chat(request)
```

所以当前 HTTP 接口的返回协议没有改变。

## 13. 数据分别保存在哪里

| 数据 | 生成期间 | 成功后 | 失败后 | 取消后 |
|---|---|---|---|---|
| 用户消息 | PostgreSQL + Redis | 保留 | 保留 | 保留 |
| 助手占位 | PostgreSQL PENDING | 更新为 COMPLETED | 更新为 FAILED | 更新为 CANCELLED |
| delta 片段 | Service 内存 | 合并后一次写 PostgreSQL | 部分正文一次写 PostgreSQL | 部分正文一次写 PostgreSQL |
| 成功助手消息 | 无 | PostgreSQL + Redis | 无 | 无 |
| 非成功助手消息 | 无 | 无 | 仅 PostgreSQL | 仅 PostgreSQL |
| 摘要任务 | 无 | 尽力调度 | 不调度 | 不调度 |

## 14. 本模块没有数据库迁移

本模块复用已有字段：

- `messages.content`
- `messages.status`
- `messages.error_code`
- `messages.error_message`
- token 字段和 `finished_at`

只新增 Python Service 事件和错误码，没有修改表结构，因此不需要执行 Alembic migration。

## 15. 当前还没有实现什么

- 前端逐段渲染和按钮禁用。
- 跨进程恢复普通聊天生成。
- `llm_runs` 模型调用审计表。

FastAPI SSE 路由和文本帧编码已在[第 24 模块](24-fastapi-sse-transport.md)完成。
可正常传播的取消和断线终态已在[第 25 模块](25-stream-cancellation-state.md)完成。

## 16. 测试保护什么

- `stream_message` 是异步生成器。
- user 和 pending assistant 在 started 前共同提交。
- 第二条消息创建失败会回滚，且不调用 Provider。
- delta 顺序和助手消息 ID 正确。
- delta 期间不 UPDATE 消息表。
- completed 后只写一次完整助手终态。
- 成功消息进入 Redis 并调度摘要。
- 失败时保存部分正文和安全错误。
- 失败助手不进入 Redis、不调度摘要。
- Provider 静默结束会成为协议错误。
- delta 与 completed 正文不一致会成为协议错误。
- Provider 直接抛超时也能保存为 `MODEL_TIMEOUT`。
- 提前遇到终态会关闭 Provider 生成器。
- 原非流式超时、重试和会话功能继续通过。

## 17. 常见错误

- 直接把 Provider 事件发送给前端，丢失消息 ID 和持久化语义。
- user 与 assistant 占位分两次提交，留下孤立消息。
- 模型失败就删除用户问题，失去审计和重试依据。
- 每个 delta 都提交数据库，造成写放大。
- 收到 completed 前就把消息标记成功。
- 失败的部分回答写进 Redis，污染下一次模型上下文。
- 不验证 delta 与最终正文一致性。
- 跳出 async for 后不关闭底层生成器。

## 18. 面试表达

> Provider 流只表达模型 Chunk，我在 Conversation Service 再定义 started、delta、completed 和 error 业务事件。用户消息与 pending assistant 在一个事务提交；模型调用位于事务外，避免长事务。delta 只在内存累积，成功或失败终点各写一次数据库。completed 还会校验片段拼接与最终正文一致，失败的部分正文只留 PostgreSQL，不进入 Redis 或摘要。Service 提前读到终态时通过 aclosing 及时释放 Provider 的 HTTP 流。

## 19. 小练习

请解释下面两个失败为什么处理不同：

```text
A：user 创建成功，但 assistant 占位创建时报数据库错误
B：user 和 assistant 已提交，模型输出“部分回答”后断线
```

回答三个问题：

1. A 为什么要回滚 user？
2. B 为什么不能删除 user？
3. B 的“部分回答”保存在哪里，为什么不能进入 Redis？

答案要点：A 的两条消息共同表达一次生成开始，事务尚未成功；B 的用户问题已经是事实，需要保留并让同一助手消息可重试；部分正文以 FAILED 消息保存在 PostgreSQL，但不能进入只包含已完成上下文的 Redis。
