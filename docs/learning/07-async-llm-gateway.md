# 模块 07：异步 LLM Gateway 基础

## 要解决的问题

会话服务需要调用模型，但不应该知道 DeepSeek、OpenAI 或 Mock Provider 使用哪一种 SDK，更不应该
负责把同步调用塞进线程。否则每个调用模型的业务方法都会混入厂商适配和并发细节。

本模块将统一接口改为异步：`await provider.chat(request)`。`ConversationService` 只负责编排
业务状态；Provider 负责使用具体 SDK 发出请求。

## 调用链

```text
ConversationService
→ BaseChatProvider.chat（异步抽象）
→ MockProvider / DeepSeekProvider / OpenAIProvider
→ AsyncOpenAI 或本地模拟响应
→ ChatResponse
```

## 关键设计

### 为什么 Service 不再使用 asyncio.to_thread

旧实现中，Service 必须知道 Provider 是同步函数，并通过 `asyncio.to_thread()` 避免阻塞事件循环。
这会让业务层依赖基础设施细节。现在 DeepSeek Provider 使用官方 `AsyncOpenAI` 客户端，等待网络
响应时会把控制权还给事件循环；Service 只等待统一的异步接口。

若某个厂商只有同步 SDK，也应由该 Provider 自己在内部使用线程适配，而不是让 Service 做这件事。
这样未来替换 SDK 时，业务代码不需要变化。

### Mock Provider 的作用

Mock Provider 同样实现异步接口，但不访问网络，稳定返回固定结果。它用于本地开发、单元测试和
演示环境，使测试不依赖 API Key、网络或模型限流。

## 后续能力

- 流式 `stream_chat()` 已在[模型流式输出的内部契约](22-internal-llm-stream-contract.md)中实现。
- 尚未实现超时、重试、取消、token 计量的统一网关策略。
- `OpenAIProvider` 仍是异步预留入口，真实请求实现将在接入真实 Provider 时完成。

这些能力要建立在统一异步边界之上，因此放在后续小步中完成。

## 测试覆盖

- 抽象 Provider 的 `chat` 是协程函数。
- Mock Provider 可以通过 `await` 返回预期响应。
- 模型超时仍会更新消息失败状态。
- 原位重试成功与失败流程继续通过，证明 Service 确实能等待异步 Provider。

## 面试表达

“我用 Provider Adapter 隔离模型厂商差异。业务层只依赖异步聊天接口，网络 SDK、同步兼容和厂商
参数转换都留在基础设施层；这样流式响应、超时策略和模型切换不会污染会话业务代码。”

## 小练习

假设接入一个只有同步 `chat()` SDK 的模型厂商。请说明适配代码应该放在哪里，并写出一句伪代码。

答案要点：放在该厂商自己的 Provider 内部，例如：
`response = await asyncio.to_thread(self.client.chat, payload)`。Service 仍然只写
`await self.provider.chat(request)`。
