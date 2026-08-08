# Vue 会话 API Client 与数据契约

## 1. 本模块解决什么问题

后端已经提供会话创建、列表、更新和删除接口，但 Vue 页面不应各自编写 `fetch`、URL、Token 和 JSON 转换。

本模块建立一个很小的前端接口层：

```text
Vue 页面 / Pinia Store
→ conversations.ts
→ client.ts 的 requestJson
→ FastAPI /api/v1/conversations
→ PostgreSQL
```

它现在不保存列表，也不决定加载动画或错误提示。这些属于下一个模块的 Pinia Store 和页面。

## 2. 代码入口

- `frontend/src/api/contracts.ts`：前后端传输数据的 TypeScript 类型。
- `frontend/src/api/conversations.ts`：会话 CRUD 的 HTTP 函数。
- `frontend/src/api/client.ts`：已有的公共请求、认证头和错误处理。
- `frontend/src/api/conversations.test.ts`：协议行为测试。

## 3. 为什么接口层要独立

如果页面直接请求：

```text
ConversationList.vue
→ fetch(...)
→ 手写 Authorization
→ 手写 JSON.stringify
→ 手写错误处理
```

创建、归档、删除页面都会重复这些细节。以后接口路径或认证方式变化，也容易漏改。

当前设计改为：

```text
组件
→ store.createConversation(...)
→ api.createConversation(token, input)
→ requestJson(...)
```

好处是组件只关心用户操作，Store 只关心状态变化，API Client 只关心协议。

## 4. 数据契约为什么先保持 snake_case

FastAPI 的 `ConversationResponse` 返回：

```json
{
  "id": "...",
  "title": "项目会议准备",
  "model_name": "gpt-5",
  "is_archived": false
}
```

本模块的 TypeScript 类型同样使用 `model_name`、`is_archived`。

原因是它是一个直接对应 HTTP 的 DTO（数据传输对象）：前端不需要猜测字段如何变形，也没有无意义的转换代码。未来若前端需要额外计算字段，例如 `updatedAtText` 或 `isSelected`，再建立独立的 ViewModel，避免把展示状态混进 API 契约。

## 5. 四个函数的职责

| 函数 | HTTP 请求 | 负责什么 |
|---|---|---|
| `listConversations` | `GET /conversations` | 默认读取未归档会话，可带 `include_archived=true` |
| `createConversation` | `POST /conversations` | 发送新会话配置，返回创建后的会话 |
| `updateConversation` | `PATCH /conversations/{id}` | 修改标题或归档状态 |
| `deleteConversation` | `DELETE /conversations/{id}` | 删除会话，接受 204 空响应 |

每个函数都接收 `token`，再交给公共 `requestJson`。因此 Bearer Token、非 2xx 错误和网络错误只处理一次。

## 6. 为什么会话 ID 需要 encodeURIComponent

路径函数：

```ts
function conversationPath(conversationId: string): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}`;
}
```

正常 UUID 不会出现 `/`。但把编码集中到这里可以防止特殊字符被当成额外路径分隔符，例如：

```text
会议 / 1
→ %E4%BC%9A%E8%AE%AE%20%2F%201
```

这既是协议正确性，也让调用者无需重复考虑 URL 细节。

## 7. POST、PATCH 为什么手动 JSON.stringify

`fetch` 不会自动把普通对象转换为 JSON。

```text
JavaScript 对象
→ JSON.stringify
→ requestApi 检测 string body
→ 自动添加 Content-Type: application/json
→ FastAPI Pydantic 校验
```

如果没有 `JSON.stringify`，浏览器不会按后端期望发送 JSON 请求体；如果字段不合法，FastAPI 仍会返回 422，这是后端最终校验。

## 8. 测试保护什么

测试没有 mock 掉 `requestJson`，而是 mock 浏览器的 `fetch`。因此会同时验证 API Client 与公共请求层之间的真实协作：

- 默认列表请求路径正确，并发送 Bearer Token。
- 包含归档会话时，查询参数为 `include_archived=true`。
- 创建会话发送正确的 POST JSON 正文，并接受 201。
- 更新和删除对 ID 做 URL 编码，删除能处理 204 空响应。

这类测试是前端协议测试：它不需要启动 FastAPI，却能较早发现路径、HTTP 方法、字段名和认证头写错的问题。

## 9. 本模块没有做什么

- 没有 Pinia 会话 Store。
- 没有会话列表或新建会话页面。
- 没有消息历史和 SSE 聊天界面。
- 没有数据库表或 Alembic 迁移。

下一小步才会把这些 API 函数包装进 `ConversationStore`，实现列表加载、创建后的本地插入、归档和删除后的状态同步。

## 10. 面试表达

> 我把前端会话 CRUD 先建成 API Client，而不是让组件直接调用 fetch。接口层复用统一的 requestJson，因此认证头、JSON、网络错误和业务错误都有一处实现。数据类型先保持和 FastAPI 的 DTO 一致，避免无价值的字段转换；页面状态会在下一层 Pinia Store 管理。还对 URL 编码、查询参数、POST/PATCH 正文和 DELETE 204 写了协议测试。

## 11. 小练习

请补充一个 `getConversation(token, conversationId)` 函数和测试，要求：

- 请求 `GET /api/v1/conversations/{id}`。
- 复用 `conversationPath`。
- 传递 Bearer Token。
- 返回 `ConversationResponse`。

答案要点：它不应直接 `fetch`，而应调用 `requestJson<ConversationResponse>(conversationPath(conversationId), { method: "GET", token })`。
