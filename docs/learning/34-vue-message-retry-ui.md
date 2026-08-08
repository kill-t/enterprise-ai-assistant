# Vue 失败消息原位重试

## 1. 本模块解决什么问题

模型超时、网络中断或用户停止生成后，助手消息可能处于：

```text
failed
cancelled
```

用户不应该重新输入原问题，也不应该产生额外的助手消息。正确行为是原位重试：

```text
原助手消息 ID 不变
消息列表位置不变
审计对象不变
状态和正文更新为本次结果
```

## 2. 后端接口的真实协议

接口：

```text
POST /api/v1/conversations/{conversationId}/messages/{messageId}/retry
```

这个接口目前不是 SSE。后端依次执行：

```text
校验当前用户、会话和消息
→ 只允许 assistant + failed/cancelled
→ 原消息更新为 pending，清空旧正文和错误
→ 调用模型
→ 原消息更新为 completed 或 failed
→ 返回最终 MessageResponse JSON
```

因此前端不会收到 retry 的 `delta`。重试按钮在整个请求期间禁用，等待最终 JSON 返回。这与“新问题的流式发送”不同，是当前后端接口的明确边界。

## 3. 调用链

```text
失败/取消助手消息卡片
→ 重试按钮
→ ConversationDetailStore.retryMessage(messageId)
→ retryConversationMessage(token, conversationId, messageId)
→ POST retry
→ FastAPI ConversationService.retry_failed_message
→ PostgreSQL 原位更新同一 MessageEntity
→ 返回最终 MessageResponse
→ Store 替换同一 ID 的消息
```

浏览器不传 `user_id`，也不能指定别人的消息；FastAPI 从 JWT 得到当前用户并验证消息属于该会话。

## 4. 前端状态流转

点击重试后，页面先把原消息临时变为：

```text
content        ""
status         pending
error_code     null
error_message  null
finished_at    null
```

并将 `messageId` 放进：

```text
retryingMessageIds
```

消息卡片保留在原位置，按钮显示：

```text
正在重试…
```

请求完成：

```text
成功：使用后端返回 MessageResponse 替换同一 ID
失败：恢复开始前保存的原消息快照，并显示本次 retry 错误
```

没有新增一条用户消息或助手消息。

## 5. 为什么要保存原消息快照

重试开始时先清空局部显示。如果请求本身遇到网络错误：

```text
请求甚至可能没有到达 FastAPI
```

此时若不保存原值，用户原本看到的失败原因与部分正文都会被清掉，页面只剩一个无信息的 pending 气泡。

当前 Store 在请求前保存 `originalMessage`：

```text
HTTP 成功 → 使用服务端最终消息
HTTP 失败 → 恢复 originalMessage
```

同时把本次错误写入 `retryErrorMessages[messageId]`，所以用户能区分：

- 原模型调用为什么失败。
- 这一次点击重试为什么没能发出或没能完成。

## 6. 为什么只禁用当前消息按钮

Store 使用：

```text
retryingMessageIds: string[]
```

并提供：

```ts
isRetryingMessage(messageId)
```

这样页面可精确控制：

```text
消息 A 重试中 → A 的按钮禁用
消息 B（若未来允许多个失败消息）→ 不被 A 的状态误禁用
```

当前还会在全局 `isSending` 时禁用重试，避免普通流式生成与同步 retry 同时占用同一个会话详情状态。后端仍会最终拒绝不合法状态，这是前端体验保护之外的第二层安全边界。

## 7. 哪些消息显示重试按钮

仅满足以下条件：

```text
role === assistant
并且 status === failed 或 cancelled
```

不会给以下消息显示按钮：

- 用户消息：它不是模型输出。
- system/tool 消息：语义不一定是可重放的 LLM 回复。
- pending 消息：已有请求正在处理。
- completed 消息：已经成功完成。

重试过程中消息状态已经变为 pending，但按钮仍保留显示“正在重试…”，否则用户会误以为按钮突然消失或操作丢失。

## 8. 与 SSE 发送的区别

| 新发送消息 | 原位重试 |
|---|---|
| `POST /messages/stream` | `POST /messages/{id}/retry` |
| SSE `started/delta/completed/error` | 一次最终 JSON 响应 |
| 新建用户消息和新助手消息 | 复用已有助手消息 ID |
| 可以停止生成 | 当前接口不支持客户端停止 retry |
| 正文逐段显示 | 等待最终结果 |

未来如果产品要求“重试也逐段展示”，后端需要新增 retry stream 接口，而不是在前端伪造 SSE。

## 9. 测试保护什么

API Client 测试：

- 使用 POST。
- 正确编码会话 ID 和消息 ID。
- 传递 Bearer Token。
- 返回最终 MessageResponse。

Store 测试：

- 重试开始后同一消息 ID 转为 pending。
- 重试期间再次调用不发送第二次请求。
- 成功后同一消息 ID 变为 completed。
- 网络或 5xx 失败时恢复原消息。
- 单独保存本次 retry 错误。

组件测试：

- failed 助手消息显示重试按钮。
- 点击后按钮显示“正在重试…”并被禁用。
- 原失败正文暂时消失，状态显示“生成中”。
- 最终结果出现后重试按钮消失。

## 10. 本模块没有做什么

- retry 没有流式 delta。
- retry 没有停止按钮。
- 没有编辑原用户问题后重试。
- 没有消息与原用户问题的显式外键关联；当前后端按时间顺序找上一条用户消息。
- 没有 Markdown、引用或工具调用消息展示。
- 没有真实 Docker 演示账号的浏览器端到端测试。

下一步建议做会话管理 UI：重命名、归档、删除和归档列表筛选。之后再进入 RAG 文档上传与可追溯引用。

## 11. 面试表达

> 我把失败模型消息设计成可恢复状态，而不是单次 HTTP 异常。重试 API 复用原助手消息 ID，前端在请求开始时把该消息原位切到 pending 并单独禁用按钮。因为后端 retry 目前是同步 JSON，我会等待最终 MessageResponse；如果请求失败，Store 用快照恢复旧消息并展示本次 retry 错误。后端继续校验角色和消息状态，前端禁用只是体验层保护。

## 12. 小练习

请解释：为什么 retry 请求网络失败时要恢复旧 failed 消息，而不是把消息一直留在 pending？

答案要点：网络失败不能证明后端已经开始或完成重试；一直 pending 会误导用户“系统仍在生成”。恢复旧消息能保留上次可靠状态与原错误，用户可再次决定是否重试或刷新页面确认服务端终态。
