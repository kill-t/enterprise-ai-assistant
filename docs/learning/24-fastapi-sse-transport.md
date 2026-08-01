# FastAPI SSE 流式协议层

## 1. 本模块解决什么问题

前两个模块已经完成：

```text
Provider 流：delta → completed / error
Service 流：started → delta → completed / error
```

但浏览器不能直接读取 Python 异步生成器。本模块增加 HTTP 入口：

```text
POST /api/v1/conversations/{conversation_id}/messages/stream
```

路由把 Service 事件编码为 SSE 文本帧，再通过 `StreamingResponse` 逐段发给客户端。

本模块只做“业务事件如何通过 HTTP 传输”，不负责模型调用、数据库事务或 Redis。

## 2. 完整调用链

```text
未来的 Vue fetch 请求
→ FastAPI stream_message 路由
→ ConversationService.stream_message
→ 预取 started 事件
→ StreamingResponse
→ iter_sse_frames
→ encode_sse_event
→ 浏览器逐帧读取
```

三层流各有不同职责：

| 层 | 事件或格式 | 负责什么 |
|---|---|---|
| Provider | `delta/completed/error` | 统一不同模型厂商的输出 |
| Service | `started/delta/completed/error` | 消息 ID、状态、持久化和业务失败 |
| HTTP SSE | `event:` 与 `data:` 文本 | 让浏览器按协议收到业务事件 |

路由只做协议转换，因此未来即使把 SSE 换成 WebSocket，Service 业务规则也不需要重写。

## 3. SSE 文本帧是什么

一个 `delta` 事件在线路上类似：

```text
event: delta
data: {"type":"delta","message_id":"...","delta":"会议"}

```

含义是：

- `event:` 是事件名称。
- `data:` 是该事件携带的 JSON 数据。
- 末尾空行表示这一帧结束。

当前编码器使用 `event.model_dump_json()`。正文中的真实换行会被编码成 JSON 的 `\n`，所以不会错误地把一条 `data:` 拆成多行。

## 4. 为什么必须先预取 started

`StreamingResponse` 一旦发送 HTTP 响应头，状态码通常已经确定为 200。之后即使发现会话不存在，也不能再把同一个响应改成 JSON 404。

错误做法：

```text
先返回 StreamingResponse 200
→ Service 才检查会话所有权
→ 抛出 404
→ HTTP 状态已经无法修改
```

当前做法：

```text
在路由函数内 await 第一项
→ 所有权、上下文预算和消息对提交成功
→ 确认第一项是 started
→ 才创建 StreamingResponse
```

因此错误被分成两类：

| 发生时间 | 返回方式 | 示例 |
|---|---|---|
| `started` 之前 | 普通 HTTP JSON 错误 | 会话不存在、数据库写入失败 |
| `started` 之后 | HTTP 仍为 200，以 SSE `error` 作为终点 | 模型超时、模型流中断 |

这不是把模型错误当成功，而是因为一个 HTTP 响应只能发送一次响应头。客户端必须同时检查 HTTP 状态和 SSE 终点事件。

## 5. started 为什么是流的第一项

`started` 表示：

```text
用户消息已保存
+ pending 助手消息已保存
+ 客户端获得稳定的助手消息 ID
```

如果第一项直接是 `delta`，前端不知道片段属于哪一次可靠的消息创建过程。路由会把这种情况视为内部协议错误，在 SSE 开始前返回 JSON 500，并关闭错误的业务流。

## 6. 为什么接口使用 POST

发送消息需要：

- JSON 请求体中的用户内容。
- Bearer Token 身份认证。
- 会话 ID。

浏览器原生 `EventSource` 主要面向 GET，而且不方便设置 Bearer Token 和 JSON 请求体。因此当前设计采用：

```text
POST + fetch + ReadableStream
```

后续 Vue 会用 `fetch` 发起请求，再从 `response.body` 逐块读取和解析 SSE，而不是直接使用原生 `EventSource`。

## 7. 三个关键响应头

当前响应包含：

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no
```

- `text/event-stream` 告诉客户端这是 SSE 数据。
- `no-cache` 避免中间缓存保存动态消息流。
- `X-Accel-Buffering: no` 提示 Nginx 不要攒满缓冲区后才一次返回。

第三个响应头只解决常见 Nginx 缓冲问题。真实部署仍需要检查 Nginx、网关和云平台是否有额外缓冲或超时配置。

## 8. 为什么要主动关闭异步生成器

HTTP 客户端正常读完、编码过程异常或连接提前结束时，`iter_sse_frames` 都会进入 `finally`：

```python
try:
    async for event in remaining_events:
        yield encode_sse_event(event)
finally:
    await close_message_stream(remaining_events)
```

主动调用 `aclose()` 会继续触发 Service 和 Provider 的 `finally`，尽早释放模型 HTTP 连接等资源。

但要注意：释放生成器资源不等于“取消状态已经完整实现”。客户端在生成中断线时，当前 pending 助手消息仍可能缺少明确终态，这会在下一个模块专门处理。

## 9. 正常与失败流程

正常流程：

```text
HTTP 200
→ started
→ delta
→ delta
→ completed
→ 关闭业务流
```

模型失败流程：

```text
HTTP 200
→ started
→ delta（可能没有）
→ error（包含 FAILED 助手消息）
→ 关闭业务流
```

预检失败流程：

```text
路由预取第一项
→ Service 抛出 BusinessException
→ 全局异常处理器返回 JSON 4xx/5xx
→ 不建立 SSE
```

## 10. 测试保护什么

- SSE 帧包含正确的 `event:`、`data:` 和结尾空行。
- 正文换行留在单行 JSON 中，不破坏帧格式。
- 路由按照 `started → delta → completed` 原顺序输出。
- 用户 ID、会话 ID 和正文被正确传给 Service。
- 模型失败以 `error` SSE 终点传输。
- 第一项之前的业务错误仍返回 JSON 404。
- 第一项不是 `started` 时返回 JSON 500 并关闭业务流。
- 响应包含防缓存和防 Nginx 缓冲头。
- OpenAPI 暴露认证要求和 `text/event-stream` 响应。

测试使用 HTTPX 的 ASGI 传输，可以验证路由、帧内容和顺序；它不等于真实网络，不负责证明每个片段经过 Nginx 后仍会立即到达浏览器。后续还需要实际浏览器和部署环境的端到端测试。

## 11. 本模块没有数据库迁移

本模块没有新增表或字段，只增加路由、SSE 编码器和测试，所以不需要执行 Alembic migration。

## 12. 当前没有实现什么

- Vue 的 `fetch + ReadableStream` 解析和逐段渲染。
- 用户点击取消后的状态更新。
- 浏览器断线后 pending 消息的可靠终态。
- SSE 心跳和代理长连接超时处理。
- `Last-Event-ID` 断点续传。
- 真实 Nginx 环境中的逐片段延迟测试。

当前流可以完整成功或以 Service `error` 结束，但主动取消和意外断线还不是完整业务能力。

## 13. 常见错误

- 在发送响应头后才检查会话所有权，导致错误只能表现为 200 流中断。
- 直接拼接用户正文而不做 JSON 编码，让换行破坏 SSE 帧。
- 把 `event: error` 误认为可以修改已经发送的 HTTP 状态码。
- 路由重复实现数据库事务和模型调用，破坏分层。
- 忘记关闭异步生成器，长期占用模型连接。
- 使用 Nginx 默认缓冲，前端在模型完成后才一次看到全文。
- ASGI 单元测试通过后就认为真实代理一定会逐片段传输。

## 14. 面试表达

> 我把模型流、聊天业务流和 HTTP SSE 分成三层。FastAPI 路由会先预取 Service 的 started 事件，确保所有权检查和消息对事务在响应头发送前成功，因此预检错误仍能返回正确 JSON 状态码。流开始后的模型失败则以 error 终点事件表达。SSE 帧使用 Pydantic JSON 编码，并关闭缓存和 Nginx 缓冲；路由 finally 主动关闭异步生成器，释放下游模型连接。

## 15. 小练习

请用自己的话回答：

1. 为什么不能一进入路由就立即返回 `StreamingResponse`？
2. 为什么模型在两个 delta 之后超时，HTTP 状态仍然是 200？
3. 为什么当前 POST SSE 后续使用 `fetch`，而不是直接使用 `EventSource`？

答案要点：响应头一旦发送便不能更改，所以先预取 started；流中错误用终点事件表达，客户端必须检查 SSE 事件；发送消息需要 JSON 请求体和 Bearer Token，`fetch` 更适合该接口。
