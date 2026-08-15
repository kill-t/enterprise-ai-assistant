# 模块 76：DeepSeek 输出截断、思考预算与失败恢复

## 1. 本模块解决什么问题

真实 PDF 联调时，用户询问国家助学金申请条件，页面回答停在“且为在校”。这类现象不能直接归因于
前端、向量检索或文档切块，必须沿真实数据链逐层定位。

最终证据是：

- 页面和 PostgreSQL 中的正文都只有 190 个字符，排除前端隐藏正文。
- `message_citations` 保存了 PDF 第 1、2、3 页的三个来源，排除只检索到半个条件。
- DeepSeek 调用的 `completion_tokens` 恰好等于 1024。
- 对照请求证明默认思考会消耗输出 Token，并返回 `finish_reason=length`。

因此根因是“模型达到输出上限”，而旧 Service 把厂商层的正常 HTTP 完成错误地理解成了业务回答完整。

## 2. 三种“完成”不是一回事

```text
HTTP 请求完成
→ SDK 流正常结束
→ 模型 finish_reason
→ 业务回答是否完整
```

HTTP 200 只说明厂商成功处理了请求。SDK 流结束只说明连接没有异常。只有检查 `finish_reason`，系统
才知道模型为何停止。

常见结束原因：

- `stop`：模型正常结束正文。
- `length`：输出达到 Token 上限，正文可能停在半句话中间。
- `tool_calls`：模型准备调用工具，不应当作普通文本回答完成。

当前聊天模块明确处理 `length`；工具调用会在后续 MCP/Agent 模块设计独立状态机。

## 3. 为什么 190 个汉字会消耗 1024 Token

输出预算不只可能包含用户看到的最终正文。模型启用思考时，内部推理也会占用输出预算，但项目不会
把推理内容展示或持久化给用户。

真实对照请求结果：

```text
默认模式：max_tokens=64，finish_reason=length，可见正文 0 字符
关闭思考：max_tokens=64，finish_reason=stop，正常返回可见正文
```

因此普通企业资料问答默认关闭思考：检索和引用已经提供了结构化证据，模型主要任务是整理答案，
不值得用大量隐藏推理挤占正文预算。复杂 Agent 规划以后可以单独决定是否开启思考。

## 4. 修复后的调用链

```text
Vue 发送或重试
→ ConversationService 构造 Grounded Prompt
→ DeepSeekProvider 显式发送 thinking=disabled
→ DeepSeek 返回正文、usage、finish_reason
→ finish_reason=stop
   → 保存 COMPLETED、引用、Redis 缓存并调度摘要
→ finish_reason=length
   → 保存部分正文、Token、MODEL_OUTPUT_TRUNCATED
   → 不写 Redis、不调度摘要、允许原位重试
```

## 5. 为什么输出预算改为 4096

1024 Token 对一句简短问答通常够用，但对“列出完整条件，并解释不同档次和限制”的 RAG 回答偏小。
本地真实配置改为 4096，并且上下文管理会在请求前为这 4096 Token 预留空间。

这不是越大越好：

- 上限越高，最坏费用和延迟越高。
- 输出预留越大，可用于历史消息和知识证据的输入预算越少。
- Prompt 仍应要求答案围绕问题，不应无边界复制整份文件。

当前 DeepSeek 上下文窗口足以容纳约 1200 Token 的本次 Prompt 和 4096 Token 输出，因此该配置适合
第一版演示。

## 6. 关键代码入口

- `app/core/config.py`：`DeepSeekSettings.thinking_enabled`，默认关闭普通问答思考。
- `app/infrastructure/llm/provider_factory.py`：把配置显式传给 DeepSeek Provider。
- `app/infrastructure/llm/providers/deepseek_provider.py`：向厂商传递思考开关，并保留非流式和流式
  `finish_reason`。
- `app/modules/conversation/service/conversation_service.py`：把 `length` 转换成可重试业务失败。
- `frontend/src/stores/conversation-detail.ts`：重试失败后重新读取 PostgreSQL 中的最新消息终态。

## 7. 数据库与事务如何变化

截断不是数据库异常，因此不回滚已经保存的用户问题。助手占位消息在原事务中由 `PENDING` 更新为：

```text
status = FAILED
content = 已经生成的部分正文
model_name = 厂商实际模型
prompt_tokens / completion_tokens / total_tokens = 实际用量
error_code = MODEL_OUTPUT_TRUNCATED
error_message = 模型回答达到长度上限，请重试
```

失败消息不进入 Redis，因为它不能作为可信的完整上下文；也不触发滚动摘要，避免摘要模型把半句话当成
已经确认的事实。

## 8. 为什么前端失败后要重新读取消息

“重试”接口是普通 JSON 请求。模型截断后，后端先把失败终态提交到 PostgreSQL，再返回 502。HTTP
错误响应只包含稳定错误信息，不包含完整消息对象。

如果前端简单恢复点击前快照，就会丢掉本次新生成的部分正文和错误码。因此前端在重试失败后：

1. 再请求一次消息历史。
2. 找到相同消息 ID 的最新数据库终态。
3. 使用该消息替换页面状态。
4. 如果数据库错误文字与操作错误相同，只展示一次。
5. 如果连消息历史也无法读取，才退回点击前快照。

## 9. 本模块测试保护什么

- Provider 工厂显式传递普通聊天思考开关。
- 非流式 DeepSeek 响应保留 `finish_reason=length`。
- 流式截断保存部分正文和 Token，并产生失败事件。
- 非流式重试截断原位更新同一条消息。
- 截断消息不写入 Redis、不触发摘要。
- 前端在 502 后读取数据库最新失败终态。
- 普通错误且历史读取也失败时仍能恢复旧快照。

## 10. 常见误判

### 误判一：页面看不全就是 CSS 问题

先比较数据库 `length(content)` 和页面正文。如果数据库也短，问题在更上游。

### 误判二：RAG 回答不全就是 Chunk 太小

先检查最终引用快照。若多个 Chunk 已覆盖完整事实，盲目增大 Chunk 只会增加 Prompt 噪声和成本。

### 误判三：HTTP 200 就是模型成功回答

厂商调用成功与业务答案完整是两层状态，必须检查 `finish_reason`。

### 误判四：只提高 max_tokens 就足够

提高上限能缓解当前问题，但没有 `length` 防线时，未来更长回答仍会被保存成伪成功。

## 11. 替代方案与取舍

### 自动续写

检测 `length` 后要求模型从断点继续，再拼接正文。体验更顺畅，但需要解决重复段落、续写次数、引用
一致性和额外费用，第一版不引入。

### 始终开启思考并继续提高上限

可能提升复杂推理质量，但普通 RAG 整理任务性价比低，延迟和费用更高。

### 截断后仍保存 COMPLETED

实现最简单，但会污染历史、摘要、评测和用户决策，本项目明确拒绝这种方案。

## 12. 面试表达

> 在真实 RAG 联调中，我没有把半截回答直接归因于向量切块，而是对照前端、PostgreSQL 消息、引用
> 快照和 LLM usage，定位到 DeepSeek 思考 Token 耗尽 1024 输出预算。修复时不仅把预算提高到
> 4096、默认关闭普通问答思考，还让 Provider 保留 finish_reason，并在 Service 将 length 转成可重试
> 失败，防止半截正文进入 Redis 和滚动摘要。前端重试失败后重新读取数据库终态，保证页面与事实来源
> 一致。

## 13. 小练习（15–30 分钟）

请回答：为什么检测到 `finish_reason=length` 后，不能只在前端弹一个“回答不完整”的提示，同时仍让
数据库消息保持 `COMPLETED`？请从后续上下文、Redis、滚动摘要和审计四个角度解释。

答案要点：数据库是唯一事实来源；只改前端会让刷新后丢失提示，失败正文可能进入模型上下文和摘要，
审计也会把不完整回答统计为成功。状态必须首先在 Service 和 PostgreSQL 中正确表达，前端只是展示。
