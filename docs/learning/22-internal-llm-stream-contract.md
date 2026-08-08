# 模型流式输出的内部契约

## 1. 本模块解决什么问题

此前 Provider 只有普通调用：

```python
response = await provider.chat(request)
```

模型生成十秒，调用方就等待十秒，然后一次拿到完整正文。实际聊天界面更适合边生成边显示：

```text
“会议” → “纪要” → “已整理” → “完成”
```

本模块先在 Provider 层建立统一内部流：

```python
async for event in provider.stream_chat(request):
    ...
```

它还不是浏览器 SSE，也不会实时修改数据库。它只回答一个问题：不同模型厂商产生的数据块，如何转换成项目内部一致的事件。

## 2. 三种流事件

第一版只定义三种互斥事件：

```text
ChatStreamDeltaEvent
ChatStreamCompletedEvent
ChatStreamErrorEvent
```

对应的 `type` 分别是：

```text
delta
completed
error
```

### delta

只携带本次新增的非空正文：

```json
{
  "type": "delta",
  "delta": "会议"
}
```

多个 delta 按顺序拼接，得到当前已生成正文。空字符串没有显示价值，因此 Pydantic 会拒绝空 delta；Provider 读取到 SDK 空块时直接跳过。

### completed

只在整个流正常结束后产生一次：

```json
{
  "type": "completed",
  "response": {
    "content": "会议纪要已整理完成",
    "model": "deepseek-chat",
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120,
    "finish_reason": "stop"
  }
}
```

它携带完整 `ChatResponse`，用于最终持久化、计费统计和审计。上层可以检查：

```text
所有 delta 拼接结果 == completed.response.content
```

### error

模型或网络中途失败时产生：

```json
{
  "type": "error",
  "error_code": "LLM_STREAM_ERROR",
  "error_message": "模型流式调用失败",
  "model": "deepseek-chat"
}
```

错误事件之后不会再产生 `completed`，否则上层无法判断这次回答究竟成功还是失败。

完整异常正文和堆栈只写入日志，事件只暴露稳定错误码与安全提示。

## 3. 为什么不用一个全是可空字段的模型

一种简单但容易出错的结构是：

```text
type
delta?
response?
error_code?
error_message?
```

这样可能构造出没有 delta 的 `delta` 事件，或者同时带 response 和 error 的矛盾事件。

当前使用三个 Pydantic 模型组成可辨识联合类型：

```python
ChatStreamEvent = Annotated[
    ChatStreamDeltaEvent
    | ChatStreamCompletedEvent
    | ChatStreamErrorEvent,
    Field(discriminator="type"),
]
```

`type` 是辨识字段。看到 `type=completed`，代码和类型检查器就知道该事件必须有 `response`。

## 4. 普通 async 函数是什么

普通异步函数：

```python
async def chat(request):
    response = await sdk.call()
    return response
```

调用方式：

```python
response = await provider.chat(request)
```

`await` 的含义不是“启动一个线程”，而是：当前协程等待网络结果时，把事件循环的执行机会交给其他协程。等结果完成后，本协程从原位置继续，最终只 `return` 一次。

## 5. async generator 是什么

异步生成器同时使用 `async def` 和 `yield`：

```python
async def stream_chat(request):
    yield DeltaEvent(delta="会议")
    await 等待下一个网络块
    yield DeltaEvent(delta="完成")
    yield CompletedEvent(...)
```

它不会一次返回全部结果。每执行一次 `yield`：

```text
产生一个事件
→ 暂停在当前位置
→ 调用方处理事件
→ 调用方请求下一项
→ 从 yield 后继续执行
```

所以它更像“可以异步等待的数据传送带”。

## 6. yield 和 return 的区别

```text
return response
```

表示函数结束，并交出唯一结果。

```text
yield event
```

表示暂时交出一个结果，但函数还没有结束，之后可以继续等待和产生更多事件。

异步生成器最终自然执行完毕，调用方的 `async for` 才结束。本项目不使用一个普通 `return` 值表示流完成，而是显式产生 `completed`，方便后续跨越 Service 和 SSE 协议层。

## 7. async for 做了什么

消费代码：

```python
async for event in provider.stream_chat(request):
    处理(event)
```

可以理解成重复执行：

```text
等待下一项
→ 有事件：处理事件
→ 再等待下一项
→ 生成器结束：退出循环
```

普通 `for` 只能消费立即可用的同步数据；`async for` 能在等待下一个网络块时把事件循环交给其他请求。

## 8. 为什么先做内部流，不直接写 SSE

如果一次把三层都写在一起：

```text
模型 SDK Chunk
→ Conversation Service
→ HTTP SSE 文本帧
```

测试失败时很难判断是厂商适配、业务状态还是 HTTP 协议出了问题。

当前先固定最底层：

```text
模型 SDK Chunk
→ Provider 内部流事件
```

第 23 模块已经完成：

```text
Provider 事件
→ Service 消息状态与事务
```

之后才做：

```text
Service 事件
→ FastAPI SSE
```

这样每一层都能独立测试。

## 9. BaseChatProvider 的默认流

并不是每个 Provider 都已经接入厂商原生流。基类提供兼容实现：

```text
await self.chat(request)
→ 一个包含完整正文的 delta
→ 一个 completed
```

它满足统一接口，但不能提前显示内容。真正支持原生流的 Provider 可以覆盖 `stream_chat`。

这使暂未完成的 Provider 不会迫使 Service 写厂商判断：

```python
# Service 将来只需要这一种写法
async for event in provider.stream_chat(request):
    ...
```

## 10. MockProvider 如何模拟流

默认完整正文：

```text
Hello, I'm Enterprise AI Assistant.
```

被固定拆成：

```text
"Hello, "
"I'm Enterprise "
"AI Assistant"
"."
```

每段之间执行 `await asyncio.sleep(0)`，它不会真的等待一段时间，只是主动把执行机会交还事件循环，用来模拟网络流的异步切换。

测试还可以传入：

```python
metadata={"simulate_stream_error": "true"}
```

Mock 会在产生第一段后模拟连接中断，便于稳定测试“部分输出后失败”，而不依赖真实网络。

## 11. DeepSeekProvider 如何适配 SDK Chunk

DeepSeek 使用 OpenAI-compatible 异步客户端：

```python
sdk_stream = await client.chat.completions.create(
    stream=True,
    stream_options={"include_usage": True},
    ...,
)

async for chunk in sdk_stream:
    ...
```

处理规则：

1. `choice.delta.content` 非空时，保存并产生 delta。
2. `choice.finish_reason` 非空时，保存停止原因。
3. `usage` 非空时，保存 token 用量。
4. 正常结束后拼接正文并产生 completed。
5. SDK 异常时记录日志，产生 error 后结束。

DeepSeek 官方说明，启用 `stream_options.include_usage` 后，会在结束前增加一个 `choices` 为空而 `usage` 有值的数据块。因此代码必须先读取 usage，再跳过空 choices。

参考：[DeepSeek Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)

## 12. 为什么不捕获取消异常

当前代码捕获：

```python
except Exception:
```

但不会捕获 `asyncio.CancelledError`。这很重要：以后用户点击“停止生成”时，取消信号必须继续向外传播，才能真正终止 SDK 网络流，而不是被误写成普通模型错误。

本模块当时只保护了取消信号的传播路径；取消后的数据库状态已在[第 25 模块](25-stream-cancellation-state.md)实现。

详见[Service 流式消息状态与事务边界](23-service-stream-message-state.md)。

## 13. 当前没有实现什么

- HTTP `/messages/stream` 和 SSE 帧尚未实现。
- 前端不会逐段显示。
- Provider 层不负责取消后的业务状态；该能力由第 25 模块的 Conversation Service 实现。
- 流式过程中不实时写 PostgreSQL。

当前普通聊天仍调用 `await provider.chat()`；新的 Service 流尚未接入 HTTP，因此现有接口行为不变。

## 14. 测试保护什么

- 基类 `stream_chat` 确实是异步生成器。
- 只实现普通 chat 的 Provider 会得到单个 delta 和 completed。
- 空 delta 会被 Pydantic 拒绝。
- Mock 四个片段顺序固定。
- delta 拼接结果等于 completed 完整正文。
- completed 保存 token 用量。
- 部分输出后的异常产生 error，且不会再产生 completed。
- DeepSeek 请求强制使用 `stream=True`。
- DeepSeek 请求最后的 usage 数据块。
- SDK Chunk 能正确转换正文、停止原因和 token 用量。
- SDK 原始异常不会通过 error 事件泄露。

## 15. 常见错误

- 把异步生成器写成返回列表，失去边生成边处理的意义。
- 对流式接口使用 `await`，而不是 `async for`。
- 把 SDK 的空角色块当成空正文发送给前端。
- 只发送 delta，不发送明确的成功终点。
- 发生 error 后仍发送 completed。
- 只记录片段，不在结尾形成完整响应，无法持久化。
- 直接把厂商异常正文发送给用户。
- 捕获取消异常，导致用户点击停止却无法真正中断。

## 16. 面试表达

> 我先在 Provider 层定义了厂商无关的流式契约，使用可辨识联合类型表达 delta、completed 和 error。DeepSeek 适配器通过 async for 消费 SDK Chunk，跳过空块、累计完整正文，并从最后的 usage 块记录 token。正常流一定以 completed 结束，异常流以安全 error 结束。HTTP SSE 尚未参与，因此 Provider 协议可以独立单测，后续 Service 和传输层只消费统一事件。

## 17. 小练习

假设 Provider 依次产生：

```text
delta("会")
delta("议")
delta("完成")
completed(content="会议完成")
```

请完成两个小任务：

1. 用 `async for` 写一段伪代码，只把 delta 拼接到 `text`。
2. 在收到 completed 时，检查 `text` 是否等于完整 response.content。

答案要点：循环中先用事件类型判断；delta 执行 `text += event.delta`；completed 执行 `assert text == event.response.content`。不要对整个 `stream_chat()` 使用普通 `await`。
