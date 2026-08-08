# Vue 会话详情与历史消息读取协议

## 1. 本模块解决什么问题

会话列表只能展示标题。用户进入某个会话后，还需要：

- 会话元数据：标题、模型、归档状态、更新时间。
- 已经持久化的消息历史：用户问题、助手成功回复、失败消息和取消消息。

后端已有两个受 JWT 与所有权保护的接口：

```text
GET /api/v1/conversations/{conversation_id}
GET /api/v1/conversations/{conversation_id}/messages
```

本模块在前端 API Client 添加对应函数，为下一模块的详情路由和消息页面准备稳定协议。

## 2. 调用链

```text
未来 ConversationDetailView / MessageStore
→ getConversation(token, conversationId)
→ GET /conversations/{id}
→ FastAPI current_user + Repository 所有权过滤
→ PostgreSQL conversations

未来 ConversationDetailView / MessageStore
→ listConversationMessages(token, conversationId)
→ GET /conversations/{id}/messages
→ FastAPI current_user + Repository 所有权过滤
→ PostgreSQL messages
```

前端只传 Token 和 ID，不传 `user_id`。用户身份必须从 JWT 取得，不能由浏览器提供任意用户 ID。

## 3. 两个函数分别做什么

| 函数 | 返回类型 | 适用场景 |
|---|---|---|
| `getConversation` | `ConversationResponse` | 详情页标题、模型配置、检查会话是否存在 |
| `listConversationMessages` | `MessageResponse[]` | 首次打开会话时读取已经保存的对话历史 |

它们都调用公共 `requestJson`，因此不重复实现 Bearer Header、HTTP 错误、网络错误或 JSON 解析。

## 4. 为什么历史读取不能复用 SSE 函数

项目已有 `streamConversationMessage`，但它负责：

```text
POST /messages/stream
→ started / delta / completed / error
→ 浏览器边接收边显示新生成正文
```

历史读取负责：

```text
GET /messages
→ 一次 JSON 数组
→ 已经持久化的 MessageResponse
```

两者都和消息有关，但协议、生命周期和错误处理不同。若把它们强行合并，会让“加载历史”和“发送新消息”混成一个难以测试的函数。

## 5. 为什么仍要编码 conversationId

两个读取函数复用私有 `conversationPath`：

```ts
`/api/v1/conversations/${encodeURIComponent(conversationId)}`
```

真实会话 ID 当前是 UUID，通常不含特殊字符。但路径函数被所有操作复用：读取、更新、删除和消息读取。集中编码能保证以后即使 ID 格式变更，也不会把 `/` 或空格误解释为 URL 路径结构。

## 6. 测试保护什么

`conversations.test.ts` 新增协议测试，验证：

- 会话详情请求使用 `GET /conversations/{encoded-id}`。
- 历史消息请求使用 `GET /conversations/{encoded-id}/messages`。
- 两个请求正确返回 `ConversationResponse` 与 `MessageResponse[]`。
- 消息读取也传递 Bearer Token。

此测试不启动 FastAPI；它验证前端对后端协议的承诺。后端的所有权、数据库排序和 MessageResponse 内容则由已有 Python 测试保护。

## 7. 本模块没有做什么

- 没有新页面或新路由。
- 没有把消息放入 Pinia。
- 没有渲染气泡、失败状态或取消状态。
- 没有发送新消息。
- 没有改动数据库或 Alembic migration。

下一步才会创建 `/conversations/:conversationId` 路由、详情页面和消息历史 Store；在那里会决定加载、空态、404 和刷新时的页面行为。

## 8. 面试表达

> 我把“读取历史消息”和“流式生成新消息”拆成两个协议函数。历史是普通 GET JSON，流式生成是 POST SSE；虽然都使用 MessageResponse，但它们的生命周期不同。详情 API 复用统一 requestJson 和路径编码，身份只通过 JWT 传递。这样详情页面只依赖类型安全的业务函数，不需要知道 HTTP 路径和认证头细节。

## 9. 小练习

请解释为什么前端不能请求：

```text
GET /api/v1/messages?user_id=另一个用户的 ID
```

答案要点：浏览器参数可被任意修改，不能作为授权依据。正确做法是浏览器携带 JWT，FastAPI 从 JWT 得到当前用户，再在查询中同时过滤会话 ID 和当前用户 ID。
