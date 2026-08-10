# 历史消息引用读取协议

## 1. 本模块解决什么问题

上一模块已经把回答使用的 RAG 来源写入 `message_citations`，但历史消息接口仍只读取 `messages`。
因此用户刷新页面后，只能看到答案正文，看不到答案当时依据的文件、页码和原文。

本模块扩展：

```text
GET /api/v1/conversations/{conversation_id}/messages
```

使每条历史消息增加：

```json
{
  "citations": []
}
```

有来源的助手消息返回按 S1、S2 排序的引用；普通消息和无证据拒答返回空数组。

本模块只读取已有表，不修改数据库结构，因此不需要 Alembic 迁移。

## 2. 完整调用链

```text
Vue 刷新会话页面
→ GET 历史消息接口
→ Conversation Router
→ ConversationService.get_messages
→ 校验会话属于当前用户
→ MessageRepository 一次读取全部消息
→ MessageCitationRepository 一次读取全部引用
→ Service 按 message_id 分组并按 source_order 排序
→ MessageHistoryResponse
→ 返回 JSON
```

所有权校验发生在消息和引用查询之前。会话不存在和跨用户访问仍统一返回 404，且不会继续读取历史
数据，避免泄露资源是否存在。

## 3. 为什么不能每条消息单独查询引用

假设一个会话有 20 条消息，下面这种写法会产生 N+1 查询：

```text
1 次：查询 20 条消息
20 次：分别查询每条消息的引用
总计：21 次数据库查询
```

消息越多，网络往返和数据库开销越大。当前实现固定使用两次业务查询：

```text
第 1 次：SELECT ... FROM messages WHERE conversation_id = ?

第 2 次：
SELECT ... FROM message_citations
WHERE message_id IN (?, ?, ...)
ORDER BY message_id, source_order
```

然后在 Python 内存中构造：

```text
message_id A → [S1, S2]
message_id B → []
message_id C → [S1]
```

这里的一次 `IN` 批量查询就是避免 N+1 的关键。

## 4. 为什么使用独立的 MessageHistoryResponse

现有 `MessageResponse` 同时被普通发送、失败重试和 SSE 的 `started/completed/error` 事件使用。如果直接
给它增加 `citations`，本模块会在没有实现 SSE `sources` 事件前，悄悄改变所有流式协议帧。

因此本模块新增：

```text
MessageResponse
└─ MessageHistoryResponse
   └─ citations: list[MessageCitationResponse]
```

只有历史 GET 接口使用 `MessageHistoryResponse`。这样变化范围清晰：

- 历史消息可以恢复引用。
- 现有 SSE 契约保持不变。
- 下一模块可以单独设计流式 `sources` 事件的时间和顺序。

## 5. 数据库快照字段怎样变成 API 字段

数据库使用带 `_snapshot` 的名字，提醒开发者这些字段代表回答生成时的快照：

```text
filename_snapshot
content_snapshot
retrieval_score
```

但前端不需要了解存储实现。API 返回更稳定、易懂的业务名称：

```text
filename
content
score
```

Pydantic 的 `validation_alias` 完成读取映射：

```text
ORM filename_snapshot → API filename
ORM content_snapshot  → API content
ORM retrieval_score   → API score
```

OpenAPI 文档中只出现业务字段，不出现内部快照列名。这使以后调整表结构时，前端协议不必跟着改名。

## 6. 历史引用响应包含什么

一条引用示例：

```json
{
  "id": "引用 UUID",
  "knowledge_base_id": "知识库 UUID 或 null",
  "document_id": "文档 UUID 或 null",
  "chunk_id": "Chunk UUID 或 null",
  "knowledge_base_name": "项目资料",
  "filename": "release-guide.pdf",
  "chunk_index": 3,
  "content": "发布前必须完成安全评审。",
  "heading_path": "项目 > 发布",
  "page_start": 2,
  "page_end": 2,
  "source_order": 1,
  "score": 0.93,
  "created_at": "..."
}
```

`source_order=1` 就是回答正文中的 `[S1]`。前端以后可以显示：

```text
[S1] release-guide.pdf · 第 2 页
发布前必须完成安全评审。
```

## 7. 为什么三个来源 ID 可以是 null

下面三个字段是活外键：

```text
knowledge_base_id
document_id
chunk_id
```

文档删除或重新入库后，它们可能因 `ON DELETE SET NULL` 变为空。API 必须允许 `null`，否则历史
消息会因为当前来源已不存在而整体序列化失败。

即使活外键为空，下列快照仍然存在：

```text
knowledge_base_name
filename
content
page_start / page_end
```

因此页面仍能展示过去的证据，只是不能再跳转到当前文档详情。

## 8. Service 怎样组装消息与引用

Repository 只负责数据库访问，返回引用实体列表。Service 负责业务组装：

```text
citations_by_message = {
  assistant_message_id: [S1, S2]
}
```

组装规则：

- 使用 `message_id` 把引用放到对应消息下。
- 每组按 `source_order` 排序。
- 没有引用时使用空数组，不返回 `null`。
- 消息本身仍保持原有创建时间顺序。

Repository 的 SQL 已排序，Service 仍再次按 `source_order` 排序。第二次排序成本很小，可以防止测试
替身或未来其他数据源打乱 S1、S2 顺序。

## 9. 为什么历史接口不从 Redis 读取引用

Redis 当前只保存近期完成消息，用于构建模型上下文；它不是 RAG 引用的事实来源。引用关系涉及：

- 审计和可追溯性。
- 文档删除后的历史快照。
- 稳定的 S1、S2 顺序。

这些数据必须来自 PostgreSQL。Redis 清空、过期或不可用都不能导致历史引用丢失。

## 10. 关键代码入口

- 历史消息与引用响应结构：
  `backend/app/modules/conversation/schema/message_schema.py`
- 引用批量查询：
  `backend/app/modules/conversation/repository/message_citation_repository.py`
- 所有权校验、两次查询和分组：
  `backend/app/modules/conversation/service/conversation_service.py`
- 历史 GET 路由：
  `backend/app/modules/conversation/api/conversation_router.py`
- 批量查询与协议测试：
  `backend/tests/test_message_citation_history.py`

## 11. 测试与真实数据库验收

自动测试覆盖：

- 多条消息 ID 只执行一次 `IN` 引用查询。
- SQL 按 `message_id, source_order` 排序。
- 空消息列表不会访问数据库。
- 普通消息返回 `citations=[]`。
- 引用按消息分组并稳定输出 S1、S2。
- ORM 快照列映射为公开 API 字段。
- 所有权失败后不查询消息和引用。
- OpenAPI 不泄露 `*_snapshot` 字段名。

全量回归结果：

```text
319 passed
Ruff: All checks passed!
```

真实 PostgreSQL 验收：

```text
临时创建一条助手消息和一条 S1 引用
→ ConversationService.get_messages 读取真实历史
→ 验证 filename、content、score 和 source_order
→ 整体 rollback
```

回滚前后数据库均为：

```text
messages = 10
message_citations = 0
```

没有使用 Redis、MinIO 写入或真实模型密钥。

## 12. 本模块尚未做什么

- 前端 TypeScript 还没有历史引用类型。
- Vue 还没有引用卡片。
- 流式聊天仍未启用知识库检索。
- SSE 还没有 `sources` 事件。
- 非流式发送响应仍不直接携带引用，需要刷新历史才能读取。
- 还没有来源详情跳转和文档删除后的 UI 状态。

下一小步是 **RAG-5G：流式 RAG 与 sources 内部事件**。先在 Service 层让流式聊天复用 Grounded
检索，并在正文 `delta` 前产生结构化 `sources` 事件；内部协议正确后，再包装成 HTTP SSE。

## 13. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 20 条消息执行 21 次查询 | 对每条消息单独查引用，应使用 `IN` 批量读取 |
| 普通消息返回 `citations=null` | 列表字段应使用空数组，前端无需反复判空 |
| S2 出现在 S1 前面 | 查询或组装没有按 `source_order` 排序 |
| API 出现 `filename_snapshot` | 把数据库列名直接暴露给前端，应映射稳定业务名 |
| 文档删除后历史接口 500 | 活外键被错误声明为必填，应允许 null 并展示快照 |
| 跨用户请求仍执行引用查询 | 所有权校验位置太晚 |
| 引用从 Redis 读取后过期丢失 | Redis 不是引用事实来源，应查询 PostgreSQL |
| 本步骤改变了所有 SSE 帧 | 直接修改共享 MessageResponse，应使用历史专用响应 |

## 14. 面试表达

> 我为历史消息定义了独立的引用响应类型，避免在实现 sources 事件前改变现有 SSE 契约。读取时先
> 校验会话所有权，再用一次消息查询和一次 message_id IN 引用查询避免 N+1，然后按 message_id
> 分组、按 source_order 排序。API 通过 Pydantic alias 把 filename_snapshot 等存储字段映射为
> filename 等稳定业务字段。活外键允许为空，来源删除后仍依靠文件名、页码和原文快照展示过去
> 的证据；Redis 不参与引用事实读取。

## 15. 小练习（约 15–20 分钟）

在 `backend/tests/test_message_citation_history.py` 增加：

```text
test_deleted_source_still_returns_citation_snapshot
```

练习目标：理解“活外键”和“不可变快照”的区别。

建议步骤：

1. 使用 `build_citation` 创建一条引用。
2. 把它的 `knowledge_base_id`、`document_id`、`chunk_id` 都设为 `None`。
3. 使用 `build_history_service` 返回该引用。
4. 调用 `service.get_messages(...)`。
5. 断言三个 ID 都是 `None`。
6. 断言 `filename`、`content`、`page_start` 仍然正确。
7. 运行：`uv run pytest tests/test_message_citation_history.py -q`。

答案要点：外键负责连接当前资源，快照负责还原历史事实；来源删除不应该让旧回答失去审计信息。
