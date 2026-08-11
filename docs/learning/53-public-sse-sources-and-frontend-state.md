# 公开 SSE sources 协议与前端状态

## 1. 本模块解决什么问题

上一模块的流式 Service 已经能产生 `sources`，但 HTTP SSE 层会主动过滤它，所以浏览器只能看到
回答正文，看不到实时来源。本模块正式打通：

```text
ConversationService
→ HTTP event: sources
→ TypeScript 运行时校验
→ Pinia Store
→ 对应助手消息的 citations
```

本步骤只完成协议、解析和状态保存，暂时不绘制引用卡片。

## 2. 公开后的事件顺序

有可靠知识来源时：

```text
started → sources → delta... → completed
```

有活动知识库但无可靠证据时：

```text
started → sources([]) → completed(grounded-refusal)
```

没有活动知识库时仍然允许：

```text
started → delta... → completed
```

因此 `sources` 是可选事件；一旦出现，它必须位于第一个 `delta` 之前，而且最多出现一次。

## 3. 后端公共联合类型怎样变化

上一模块刻意区分内部事件和公共事件。当前端解析器准备完成后，公共联合类型正式变为：

```text
MessageStreamEvent
├─ MessageStreamStartedEvent
├─ MessageStreamSourcesEvent
├─ MessageStreamDeltaEvent
├─ MessageStreamCompletedEvent
└─ MessageStreamErrorEvent
```

SSE 层删除 sources 过滤，不再理解业务含义，只负责序列化：

```text
event: sources
data: {"type":"sources", ...}
```

这样 Service 决定产生什么事件，HTTP Adapter 只负责 SSE 文本协议。

## 4. 为什么 TypeScript 类型还不够

TypeScript 只能检查开发时的代码，不能保证网络 JSON 合法。例如服务端或代理可能返回：

```json
{"type":"sources","message_id":"not-a-uuid","sources":[{"score":99}]}
```

如果直接使用 `as MessageStreamSourcesEvent`，错误字段会进入 Pinia。当前解析器会先验证未知 JSON，
确认每个字段满足协议后才收窄为业务类型。

## 5. sources 运行时校验规则

每条实时来源必须满足：

- 知识库、文档和 Chunk ID 是标准 UUID。
- 知识库名称、文件名和原文是非空字符串。
- `chunk_index` 是大于等于 0 的整数。
- 标题路径是字符串或 `null`。
- 页码是 `null` 或从 1 开始的整数。
- 起止页都存在时，结束页不能小于开始页。
- `source_order` 从 1 开始，必须与数组位置连续一致。
- `score` 是有限数字，范围为 `[-1, 1]`。

整个 `sources` 可以为空，表示采用了知识库约束但没有可靠证据。

## 6. 为什么还要验证 message_id

`started` 创建唯一助手占位，后续事件必须属于它：

```text
sources.message_id     = started.assistant_message.id
delta.message_id       = started.assistant_message.id
completed.message.id   = started.assistant_message.id
error.message.id       = started.assistant_message.id
```

否则来源可能挂到另一个消息气泡，或别的流覆盖当前回答。解析器遇到这种情况会抛出：

```text
UNEXPECTED_MESSAGE_ID
```

Pinia 在真实网络路径中不会收到该非法事件。

## 7. 前端事件顺序状态机

解析器维护：

```text
started / terminal / sourcesReceived / deltaReceived
```

规则包括：

- 第一项必须是 `started`，并且不能重复。
- `sources` 不能重复，也不能出现在任何 `delta` 之后。
- `completed` 或 `error` 后不能继续产生事件。
- 流结束前必须出现一个终点事件。

这是一个小型网络协议状态机，负责把乱序、串流或缺失帧尽早转换成明确错误。

## 8. 实时与历史来源怎样共用结构

前端抽取公共展示字段：

```text
MessageSource
├─ knowledge_base_id / name
├─ document_id / chunk_id
├─ filename / chunk_index
├─ content / heading_path / pages
└─ source_order / score
```

实时来源进一步保证三个活外键存在：

```text
MessageStreamSource extends MessageSource
```

历史引用允许来源删除后外键为 `null`，并增加持久化身份：

```text
MessageCitationResponse extends MessageSource
├─ id
└─ created_at
```

后续 Vue 引用卡片只依赖 `MessageSource`，不需要区分数据来自实时 SSE 还是历史 GET。

## 9. Store 怎样保存和合并来源

收到 `started` 后，Store 加入用户消息和 PENDING 助手消息，并记录助手 ID。收到 `sources` 后：

```text
确认 event.message_id 是当前助手
→ 确认目标角色是 assistant
→ message.citations = event.sources
```

Store 保留第二层归属检查，即使测试替身绕过了真实 SSE 解析器，也不会把来源写到未知消息。

当前 `completed` 使用基础 `MessageResponse`，暂时不携带 citations。如果整体替换消息，刚收到的
实时来源会丢失。因此完成合并规则是：

```text
completed 自带 citations → 使用后端值
completed 没有 citations → 保留当前消息的实时值
```

`error` 仍按失败终态直接替换，因为失败回答没有完成引用持久化，不能伪装成已审计成功引用。

## 10. 为什么 citations 暂时可选

历史 GET 保证每条消息有 `citations: []`，但 started、completed 和重试接口的基础响应当前可能没有
该字段。为避免在同一小步强制改造所有接口，采用渐进类型：

```text
MessageResponse.citations?: MessageSource[]
MessageHistoryResponse.citations: MessageCitationResponse[]
```

历史 API 内部按严格类型读取，但对现有调用方仍返回兼容的基础消息数组。等后端所有响应统一后，
可以再把基础字段收紧为必填。

## 11. 完整调用链

```text
Vue sendMessage
→ fetch POST /messages/stream
→ parseSseStream 解析文本帧
→ decodeMessageEvent 校验字段
→ 校验事件顺序和 message_id
→ Pinia 处理 started / sources / delta / completed / error
→ Vue 响应式状态更新
```

## 12. 关键代码入口

- 后端公共事件联合类型：`backend/app/modules/conversation/schema/message_schema.py`
- SSE 编码：`backend/app/modules/conversation/api/sse.py`
- HTTP 顺序测试：`backend/tests/test_conversation_sse.py`
- TypeScript 来源契约：`frontend/src/api/contracts.ts`
- 运行时校验：`frontend/src/api/conversation-stream.ts`
- Pinia 来源合并：`frontend/src/stores/conversation-detail.ts`
- 前端协议测试：`frontend/src/api/conversation-stream.test.ts`
- Store 测试：`frontend/src/stores/conversation-detail.test.ts`

## 13. 测试与验收

后端覆盖：

- HTTP SSE 不再过滤 sources。
- sources 位于 started 与 delta 之间。
- UUID 在 JSON 中序列化为字符串。
- 原有四类事件仍正常编码。

前端覆盖：

- 合法 sources 能被解析。
- 分数越界或页码非法时拒绝事件。
- message_id 不属于当前助手时拒绝整个流。
- delta 之后才出现 sources 时报告乱序。
- Store 将来源挂到对应助手消息。
- completed 不会覆盖实时来源。
- 原有取消、失败、重试和会话切换行为不回归。

最终结果：

```text
后端：323 passed，Ruff 通过
前端：72 passed
TypeScript：vue-tsc 通过
生产构建：Vite build 通过
```

本模块没有数据库迁移，不需要 PostgreSQL、Redis、MinIO 或模型密钥。

## 14. 本模块尚未做什么

- Vue 页面还没有渲染引用卡片。
- 还不能点击来源打开知识库或文档详情。
- 文档已删除时还没有“历史快照”提示。
- 没有折叠过长原文片段。
- 还没有校验模型正文中的 `[Sx]` 是否存在于来源列表。

下一小步是 **RAG-5I：Vue 引用卡片**。它会在助手消息下展示 `[S1]`、文件名、页码、标题路径和
原文片段；实时 sources 与刷新后的历史 citations 使用同一个组件。

## 15. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 后端有 sources，浏览器没有 | SSE Adapter 仍在过滤内部事件 |
| TypeScript 编译通过但运行报错 | 网络 JSON 没做运行时校验 |
| 来源挂到错误消息 | 没验证 message_id 与 started 助手 ID |
| completed 后来源消失 | 终态整体替换覆盖了实时 citations |
| sources 在正文后出现 | 状态机没有追踪 deltaReceived |
| 第二次 sources 覆盖第一次 | 没限制 sources 最多一次 |
| 分数显示为 99 或页码为 0 | 没验证后端范围约束 |
| 历史和实时需要两套组件 | 没抽取共同 MessageSource |

## 16. 面试表达

> 我把 Service 内部 sources 升级为公共 SSE 事件，并让 HTTP 层恢复成纯序列化 Adapter。前端不只
> 依赖 TypeScript，而是运行时验证 UUID、页码、连续 source_order 和分数范围，同时维护小型状态
> 机，保证 sources 位于首个 delta 前且所有事件属于 started 创建的助手消息。Pinia 把来源保存到
> 消息 citations，completed 若不携带引用则保留实时来源。历史与实时抽取共同 MessageSource，供
> 后续引用组件复用。

## 17. 小练习（约 15–20 分钟）

在 `frontend/src/api/conversation-stream.test.ts` 增加“重复 sources 事件”测试：

1. 构造合法 `started`。
2. 连续构造两个合法 `sources`，message_id 都指向同一助手。
3. 后面附上 `completed`。
4. 消费 `streamConversationMessage`。
5. 断言错误码为 `INVALID_EVENT_ORDER`。
6. 运行：`npm run test:run -- src/api/conversation-stream.test.ts`。

答案要点：第二个 sources 的字段本身完全合法，错误来自事件状态，而不是字段校验。
