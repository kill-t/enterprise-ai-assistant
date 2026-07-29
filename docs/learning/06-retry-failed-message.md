# 模块 06：失败消息原位重试

## 要解决的问题

模型调用超时后，用户不应该重新输入问题，也不应该看到多条重复的助手消息。重试应复用原来
失败的助手消息：其 ID、位置和审计对象都保持不变，只有状态与内容发生变化。

## 接口与状态流转

接口：`POST /api/v1/conversations/{conversation_id}/messages/{message_id}/retry`

```text
failed 助手消息
    ↓ 用户点击重试
pending（清空旧内容、旧错误和 token）
    ↓ 调用模型
completed（写入新内容与 token）
    或
failed（写入新的安全错误信息）
```

`pending` 和 `completed` 消息会被后端拒绝重试，返回 `409` 与
`MESSAGE_NOT_RETRYABLE`。前端禁用按钮是第一层体验保护，后端状态校验是第二层业务保护。

## 原问题如何找到

当前第一版消息表还没有 `reply_to_message_id` 字段，因此通过时间顺序查找失败助手消息前最近
的一条已完成用户消息，把它作为原问题。这适合当前严格的一问一答结构。

未来加入并行工具调用、分支回答或多 Agent 后，应在消息表中增加明确的关联字段，不能继续仅靠
时间顺序推断。

## 为什么要排除原问题

模型请求由两部分构成：历史消息和当前用户问题。重试时原用户消息已经在 PostgreSQL 与 Redis
历史中；若不排除，它会出现两次：一次作为历史，一次作为当前问题。`_get_history()` 新增
`exclude_message_id` 参数，以保证原问题只出现一次。

## 关键调用链

```text
retry_failed_message Router
→ ConversationService.retry_failed_message
→ 校验会话、消息归属、角色与 failed 状态
→ 找到原用户消息并构建去重后的上下文
→ 原助手消息更新为 pending
→ _generate_assistant_response
→ PostgreSQL 更新同一条消息
→ 成功时写 Redis；失败时保留 failed 状态
```

核心代码入口：[ConversationService](../../backend/app/modules/conversation/service/conversation_service.py)。

## 测试覆盖

- 重试成功后消息 ID 不变，且不会新增消息记录。
- 原问题在模型请求中只出现一次。
- 重试再次超时后，仍是同一条 `failed` 助手消息。
- `pending` 消息被拒绝，防止重复点击。
- 跨会话传入消息 ID 返回 404，防止越权访问。

## 面试表达

“我把模型失败当成消息生命周期中的一个可恢复状态，而不是一次 HTTP 异常。重试复用原失败消息，
从而让前端状态稳定、审计可追踪；同时在构造上下文时排除原用户问题，避免模型收到重复输入。”

## 小练习

假设产品要求“用户可编辑失败前的原问题后再重试”。请回答：现在通过时间顺序找原问题的设计还够
不够？需要增加什么数据字段和接口参数？

答案要点：不够。应增加助手消息到用户消息的明确关联，例如 `reply_to_message_id`，并让重试接口
接收可选的新文本；新文本应生成新的用户消息还是更新原用户消息，需要根据审计要求选择。企业场景
通常保留原消息，并新增一条修订后的用户消息或修订记录。
