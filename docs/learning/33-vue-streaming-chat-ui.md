# Vue 流式聊天、停止生成与消息终态

## 1. 本模块解决什么问题

上一模块只能读取已经保存的历史消息。用户输入新问题后，模型回复往往需要数秒，且内容是逐段产生的；如果等到完整回复才更新页面，体验会像页面卡住。

本模块把已有 SSE Client 接到会话详情页：

```text
用户输入问题
→ ConversationDetailView
→ ConversationDetailStore.sendMessage
→ streamConversationMessage
→ POST /messages/stream
→ started → delta... → completed / error
→ Pinia messages
→ Vue 消息气泡逐段更新
```

同时提供“停止生成”按钮。停止不是简单隐藏界面，而是通过 `AbortController` 中断当前 HTTP 流，并将已开始的助手消息标为取消状态。

## 2. 页面、Store、SSE Client 的分层

| 层 | 负责什么 |
|---|---|
| `ConversationDetailView` | 输入框草稿、发送/停止按钮、渲染消息和提示 |
| `ConversationDetailStore` | 消费事件、更新消息数组、生成状态、取消与错误收口 |
| `conversation-stream.ts` | POST、Authorization、SSE 字节解析、事件顺序和运行时字段校验 |
| FastAPI / Service | 保存用户消息和 pending 助手消息、调用模型、保存 completed/failed/cancelled 终态 |

Vue 页面不直接 `for await` SSE，也不直接访问 `AbortController`。如果组件承担这些工作，路由切换、退出登录和后续重试会变得难以统一处理。

## 3. 四类流事件如何改变页面

| SSE 事件 | 后端语义 | Store 对消息数组的操作 |
|---|---|---|
| `started` | 用户消息和 pending 助手消息已成功写入 PostgreSQL | 追加两条消息 |
| `delta` | 助手正文新增一段 | 按 `message_id` 追加 `content` |
| `completed` | 完整助手消息已持久化 | 用后端最终消息替换 pending 消息 |
| `error` | 助手失败消息已持久化 | 替换为 failed 消息，展示安全错误 |

例如模型返回：

```text
delta: “会议”
delta: “简报已”
delta: “整理完成”
completed: “会议简报已整理完成”
```

页面中间会逐段显示，最后仍以服务端 `completed.message` 为准。这样最终内容、Token、结束时间和状态都与 PostgreSQL 事实记录一致。

## 4. 为什么 started 才插入消息

用户点击发送后，前端不会立刻凭空构造“已发送成功”的消息，而是等到：

```text
SSE started
```

Service 只会在用户消息与 pending 助手消息数据库事务成功后发送这个事件。因此页面看到 `started` 时，可以认为两条消息已经有服务端 ID，也可以在刷新后从历史接口恢复。

如果 HTTP 在 `started` 前就失败，页面只显示发送错误，不伪造一条无法恢复的本地消息。

## 5. delta 为什么只更新内存

每一个 `delta` 都写 PostgreSQL 会导致大量小事务：

```text
“会” → UPDATE
“议” → UPDATE
“纪” → UPDATE
...
```

当前设计是：

```text
delta 只更新 Pinia 内存，得到实时视觉效果
completed/error/cancelled 由后端写最终状态
详情页下次加载从 PostgreSQL 读取终态
```

这在实时体验与数据库一致性之间更平衡。`completed` 到来时前端会用服务端返回的完整 `MessageResponse` 替换临时内容。

## 6. 停止生成如何工作

点击停止：

```text
ConversationDetailView
→ detailStore.cancelActiveStream()
→ AbortController.abort()
→ 浏览器 HTTP 流抛出 AbortError
→ Store 将当前 pending 助手消息标记为 cancelled
→ 后端断线处理保存取消终态
```

页面区分：

```text
isSending     是否存在正在消费的流
isCancelling  是否已经请求停止、等待流真正结束
```

停止按钮在 `isCancelling` 时不可重复点击。最终状态统一由 `sendMessage` 的 `catch/finally` 收口，避免按钮和消息状态各自随意修改。

## 7. 为什么 AbortController 不放进 Pinia state

`AbortController` 是浏览器对象，不是可序列化业务数据。Pinia state 应主要保存可观察、可调试的业务状态，例如：

```text
messages
isSending
isCancelling
sendErrorMessage
```

当前实现使用内部 `WeakMap` 按 Store 实例保存 controller：

- 页面无法直接误改它。
- Store 销毁后 WeakMap 条目可被垃圾回收。
- Pinia Devtools 不会尝试序列化浏览器控制对象。

## 8. 路由切换与流的关系

用户可能在 A 会话生成中跳到 B：

```text
A 正在 SSE
→ 路由变为 B
→ detailStore.load(B)
→ cancelActiveStream()
→ 清空 A 的详情并读取 B
```

即使 A 的某个事件很晚返回，Store 也检查：

```text
this.conversationId === activeStream.conversationId
```

不匹配就不写入当前页面。上一个模块的 `loadRequestVersion` 防止“详情读取”乱序；本模块的 `AbortController + conversationId` 防止“流式发送”写错会话。两者互补。

## 9. 输入框状态

`draftContent` 是组件内部 `ref`：

- 未发送草稿只属于当前页面，不应写入数据库或 Pinia 历史。
- 非空提交后立即清空输入框。
- `started` 事件带回真正持久化的用户消息。
- 发送中禁用 textarea，防止一次流尚未结束又启动第二条。
- 空输入由 Store 返回“请输入消息内容”。

页面显示 `sendErrorMessage`，它与详情读取的 `errorMessage` 分开：前者表示“本次发送失败”，后者表示“无法读取整个会话”。

## 10. 测试保护什么

Store 测试：

- `started + delta + completed` 拼接后以终态替换消息。
- `error` 事件保留 failed 助手消息和安全错误提示。
- 调用取消后 pending 助手消息标为 cancelled。
- 无 Token、路由切换、详情加载竞态仍由既有测试覆盖。

组件测试：

- 填写输入框并提交后，使用去空格后的正文调用流式 Client。
- 新用户消息和完成的助手回复出现在消息列表。
- 提交后草稿输入框清空。

这些测试使用异步生成器模拟事件序列，不需要真实模型 Key，也不会把测试数据写入数据库。

## 11. 本模块没有做什么

- 没有自动滚动到最新消息。
- 没有失败消息“重试”按钮；后端已有 retry 接口，前端会单独接入。
- 没有 Markdown 渲染、代码块、引用卡片或工具调用展示。
- 没有多模态文件上传。
- 没有真实 Docker 演示账号的浏览器 E2E。
- 还未使用 RAG、MCP 或多 Agent；当前先把基础聊天生命周期做可靠。

下一步建议实现“失败/取消助手消息的原位重试 UI”，复用后端已提供的 retry 接口，并保证重试期间按钮禁用、原助手消息原位恢复，不重复创建消息。

## 12. 面试表达

> 我把流式聊天拆成 SSE Client、Pinia Store 和 Vue 页面。Store 将 started、delta、completed、error 转换为 MessageResponse 数组，页面只渲染状态。started 代表两条消息已持久化，delta 只更新内存，终态以服务端返回为准。我还用 AbortController 做停止生成，用 WeakMap 保存不可序列化 controller，并在路由切换时取消旧流，防止 A 会话的延迟事件写到 B 会话。

## 13. 小练习

请解释：为什么用户点击发送时不直接把一条“已完成的助手消息”插入页面，而要等待 `started` 和后续 `delta`？

答案要点：发送是否被服务端接受、用户消息和 pending 助手消息是否已经持久化，只有 started 才能确认；模型回复本来是逐段生成的，直接伪造 completed 会错误地表示数据库和模型都已成功完成。
