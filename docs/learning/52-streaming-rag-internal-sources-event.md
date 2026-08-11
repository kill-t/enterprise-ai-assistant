# 流式 RAG 与内部 sources 事件

## 1. 本模块解决什么问题

前面的 RAG 聊天能力只接入了非流式发送和失败消息重试。用户实际从 Vue 页面发送消息时使用 SSE，
流式 Service 仍然只把原问题交给模型，没有检索会话绑定的知识库。

这会产生不一致：

```text
非流式请求：根据企业资料回答并保存引用
流式请求：不读取企业资料，按普通模型知识回答
```

本模块让流式聊天复用同一个 Grounded 准备过程，并新增 Service 内部 `sources` 事件。

## 2. 三条流式路径

### 没有活动知识库

```text
started
→ delta...
→ completed
```

保持原来的普通流式聊天，不产生无意义的来源事件。

### 有活动知识库且检索到可靠证据

```text
检索与 Prompt 预算检查
→ 创建用户消息和 PENDING 助手占位
→ started
→ sources(S1、S2...)
→ Provider delta...
→ completed
→ 回答与相同来源原子保存
```

### 有活动知识库但没有可靠证据

```text
检索完成但结果为空
→ 创建用户消息和 PENDING 助手占位
→ started
→ sources([])
→ completed(grounded-refusal)
```

无证据是一条正常业务结果，不启动聊天模型，也不伪造逐字输出。

## 3. 为什么检索发生在 started 之前

路由会在发送 HTTP 200 响应头之前预取第一个 `started` 事件。因此所有可能产生明确 HTTP 错误的准备
工作都应该发生在 `started` 之前：

```text
会话所有权校验
知识库绑定读取
Embedding 与 pgvector 检索
Grounded Prompt 构建
Token 预算检查
用户消息与助手占位原子创建
```

如果 Embedding 超时、向量配置冲突或 Prompt 超预算，系统可以直接返回 409、422 或 503，且不会
留下半套消息。若先发 `started` 再检索，HTTP 200 已经开始，后续业务错误只能表现为流突然断开。

## 4. 为什么 sources 必须早于第一个 delta

来源编号参与模型回答：

```text
“发布前必须完成安全评审 [S1]。”
```

如果正文先到、来源后到，前端暂时无法解释 `[S1]`，也可能在回答完成后才显示证据。稳定顺序为：

```text
started → sources → delta → completed
```

`sources` 中每条来源包含：

- 知识库 ID 和名称。
- 文档、Chunk ID。
- 文件名、Chunk 序号。
- 原文、标题路径、页码。
- `source_order` 和检索分数。

`source_order=1` 与 Prompt、回答和数据库引用中的 `[S1]` 完全对应。

## 5. 怎样保证事件、Prompt 和数据库引用是同一组来源

检索结果只在准备阶段确定一次：

```text
ConversationKnowledgeSearchResponse.results
→ tuple 保存进 _AssistantResponsePlan.sources
→ _MessageGenerationContext.sources
├─ GroundedPromptBuilder：生成 [S1]、[S2]
├─ MessageStreamSourcesEvent：产生内部 sources
└─ MessageCitationRepository：完成时保存引用快照
```

不能为事件、Prompt 和持久化分别再检索一次，因为数据库或绑定可能在三次调用之间变化，导致：

```text
页面 S1 = 文档 A
模型 S1 = 文档 B
数据库 S1 = 文档 C
```

当前设计通过复用同一个不可变元组避免这种审计错误。

## 6. grounding_applied 解决什么问题

空来源有两种完全不同的含义：

```text
没有活动知识库 → 普通聊天，不需要 sources 事件
有活动知识库但没证据 → Grounded 拒答，需要 sources([])
```

只判断 `sources` 是否为空无法区分它们。因此响应计划增加：

```text
grounding_applied: bool
```

- `False`：没有真正搜索活动知识库，保持普通聊天。
- `True`：本轮采用知识库约束，应产生 `sources`，即使列表为空。

这比根据模型名或固定拒答正文反推业务路径更清楚。

## 7. 为什么本模块暂时不公开 HTTP sources

现有前端 SSE 解析器只认识：

```text
started / delta / completed / error
```

如果后端突然公开 `sources`，旧解析器可能把它当成协议错误或直接忽略，且 TypeScript Store 还没有
保存来源的状态结构。

因此本模块显式区分：

```text
MessageStreamInternalEvent
→ 包含 sources，供 Service 内部使用

MessageStreamEvent
→ 暂不包含 sources，代表当前公开 HTTP 契约
```

SSE 包装层本步骤会过滤内部 `sources`，其余事件保持原样。下一模块更新 TypeScript 契约、解析器和
Store 后，再删除过滤并把 `sources` 正式加入公共联合类型。

这种做法使协议升级变成可测试的显式步骤，而不是后端修改后让前端被动出错。

## 8. 无证据为什么不产生 delta

固定拒答内容不是聊天模型逐段生成的结果。为了让它看起来“像流式”而人为拆成若干 delta，会造成：

- Provider 没调用，前端却误以为正在模型生成。
- 多一套没有价值的字符串切片逻辑。
- 取消状态和 Token 统计变复杂。

因此无证据路径直接：

```text
started → sources([]) → completed
```

前端收到 completed 后用完整消息替换 PENDING 占位即可，和极快完成的普通操作一致。

## 9. 回答和引用何时保存

流式 `delta` 只在内存中累计，不实时写 PostgreSQL。Provider 必须发送合法 completed，且 completed
完整正文与所有 delta 拼接结果一致，Service 才执行：

```text
更新助手消息为 COMPLETED
→ 整组替换 message_citations
→ 同一个 PostgreSQL 事务提交
→ 回填 Redis
→ 尽力调度滚动摘要
```

这沿用了第 50 个模块的原子持久化规则。Provider 失败、协议错误或客户端取消时，不会保存一组
看似完整但实际未完成的 RAG 引用。

## 10. 取消和断线如何执行

外层 `try` 覆盖 `started` 和 `sources` 两个 yield 点：

```text
started 后断线
sources 后、首个 delta 前断线
输出部分 delta 后断线
```

这些情况都会进入既有取消处理，通过条件 UPDATE 只把仍为 PENDING 的助手消息改为 CANCELLED。
已经完成的消息不会被取消逻辑覆盖。

## 11. 关键代码入口

- 内部 sources 数据结构和事件联合类型：
  `backend/app/modules/conversation/schema/message_schema.py`
- 流式 Grounded 编排、拒答和来源映射：
  `backend/app/modules/conversation/service/conversation_service.py`
- HTTP SSE 临时兼容过滤：
  `backend/app/modules/conversation/api/sse.py`
- 流式来源、拒答、检索失败和取消回归测试：
  `backend/tests/test_conversation_stream_service.py`
- HTTP 契约兼容测试：
  `backend/tests/test_conversation_sse.py`

## 12. 测试保护了什么

新增测试覆盖：

- 有证据时事件顺序为 `started → sources → delta → completed`。
- sources 按 S1、S2 排序，并与检索正文一致。
- Grounded Prompt 和持久化引用使用同一个来源元组。
- 有活动库但无证据时产生空 sources 和正常拒答。
- 无证据路径不启动 Provider。
- Embedding 或检索失败发生在 started 和消息创建之前。
- HTTP SSE 暂时过滤内部 sources，旧事件顺序保持不变。
- 原有取消、断线、超时、协议错误和普通聊天全部回归。

开发过程中先得到缺少 `MessageStreamSourcesEvent` 的导入失败，再实现契约使测试转绿，符合测试先行
的小步过程。

最终全量结果：

```text
323 passed
Ruff: All checks passed!
```

## 13. 真实 PostgreSQL 与 pgvector 验收

真实验收使用：

- 已有会话与知识库绑定。
- 已入库的 READY Chunk 和 pgvector 向量。
- Mock Embedding，不使用模型密钥。
- Fake Stream Provider，只模拟流式正文。

流程：

```text
用已有 Chunk 正文发起问题
→ 真实 Mock Embedding + pgvector 检索
→ 命中 4 条来源
→ 内部 sources 为 S1–S4
→ Fake Provider 返回带 [S1] 的流式回答
→ 真实 message_citations 写入 4 条引用
→ 验证事件与引用的 Chunk ID、顺序完全相同
→ 整体 rollback
```

回滚前后数据库均为：

```text
messages = 10
message_citations = 0
```

最初验收错误地假设“用于提问的 Chunk 必须是第一名”。Mock 哈希向量只能用于稳定测试和演示，
不能保证真实语义排序；正确验收目标应是“可靠结果存在，事件与保存顺序一致”。

另一个测试错误是把安全标签写成 `<knowledge_sources>`，实际 Prompt 为：

```text
<knowledge_sources trust="untrusted">
```

修正断言后真实验收通过。这两次调试都没有提交数据，Session 退出和显式 rollback 保证测试记录清空。

## 14. 本模块尚未做什么

- HTTP SSE 还没有公开 `sources` 事件。
- TypeScript 还没有 sources 契约和运行时校验。
- Pinia Store 还没有把来源挂到对应助手消息。
- Vue 还没有实时引用卡片。
- 无证据拒答的前端还没有专门提示样式。
- 没有校验模型正文引用的 `[Sx]` 是否存在于 sources。

下一小步是 **RAG-5H：公开 SSE sources 协议**。它会更新后端公共事件联合类型和编码测试，同时
更新 Vue TypeScript 契约、SSE 解析器与 Pinia 状态，使前端安全接收并保存来源；引用卡片视觉展示
再作为后续独立小步完成。

## 15. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 非流式有引用、流式没有 | 流式准备阶段没有启用 Grounded 检索 |
| 第一个 delta 之后才发 sources | 事件顺序错误，应在正文前确定来源 |
| 无知识库也总发空 sources | 没区分普通聊天与 Grounded 空结果 |
| 无证据仍调用 Provider | 把空检索错误降级成普通模型回答 |
| 检索失败后留下两条消息 | 检索发生在消息创建之后，应移到 started 前 |
| 事件来源与数据库引用不同 | 为展示和持久化重复执行了检索 |
| HTTP 突然出现前端未知事件 | 内部契约没有与公共协议分开升级 |
| Mock 第一名不是预期 Chunk | 哈希向量不保证真实语义排序，不应写过强断言 |
| sources 后断线仍保持 PENDING | 外层取消处理没有覆盖 sources 的 yield 点 |

## 16. 面试表达

> 我让流式聊天复用非流式的 Grounded 准备过程，检索、Prompt 构建和预算检查都发生在 started
> 之前，因此错误仍能返回正确 HTTP 状态且不留下半套消息。有可靠证据时内部事件严格按 started、
> sources、delta、completed 输出；无证据时不调用 LLM，直接 sources 空数组后完成拒答。Prompt、
> sources 事件和 message_citations 复用同一个最终来源元组，保证 S1、S2 审计一致。为了兼容旧前端，
> 我先区分内部和公共事件联合类型，由 SSE 层暂时过滤 sources，下一步再完成显式协议升级。

## 17. 小练习（约 15–20 分钟）

在 `backend/tests/test_conversation_stream_service.py` 增加：

```text
test_closing_after_sources_before_delta_marks_message_cancelled
```

练习目标：理解异步生成器在多个 `yield` 点被关闭时，为什么都必须进入取消终态。

建议步骤：

1. 构造一条可靠知识来源和会话检索结果。
2. 使用会阻塞在首个 delta 前的 Provider，或在读取 sources 后直接关闭生成器。
3. 依次读取 `started`、`sources`。
4. 调用 `await stream.aclose()`。
5. 断言助手消息状态为 `CANCELLED`。
6. 断言 Provider 尚未启动，或已被正确关闭。
7. 断言引用 Repository 没有保存来源。
8. 运行：`uv run pytest tests/test_conversation_stream_service.py -q`。

答案要点：`yield sources` 与 `yield started`、`yield delta` 一样都是可能暂停和被关闭的位置，取消处理
必须包住整个对外事件阶段，不能只包 Provider 的 `async for`。
