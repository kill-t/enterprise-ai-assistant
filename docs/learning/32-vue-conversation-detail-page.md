# Vue 会话详情路由与历史消息页面

## 1. 本模块解决什么问题

会话列表只告诉用户“有哪些会话”。要真正继续一段对话，用户必须能通过会话 ID 打开详情，并看见此前已经写入 PostgreSQL 的消息。

本模块完成：

```text
会话列表卡片
→ /conversations/:conversationId
→ ConversationDetailView
→ ConversationDetailStore
→ 并行 GET 会话元数据 + GET 历史消息
→ 标题、加载态、错误态、空态、消息记录
```

本模块只读取历史，不发送消息。下一模块才把已完成的 SSE 发送能力接到这里。

## 2. 路由设计

路由定义：

```text
/                              会话列表
/conversations/:conversationId 会话详情
/login                         登录页
```

`conversationId` 放在 URL 中，而不是只放在 Pinia 的原因：

- 刷新页面仍知道当前打开哪个会话。
- 可以复制链接或浏览器前进、后退。
- 后端请求能明确使用哪个会话 ID。
- 路由守卫能像首页一样保护详情页。

会话卡片使用 `RouterLink`，生成站内路径：

```text
/conversations/conversation-1
```

没有使用普通 `<a href>`，因为 `RouterLink` 是单页应用内部导航，不会重新加载整个 Vue 应用。

## 3. 完整调用链

用户点击一张会话卡片：

```text
HomeView RouterLink
→ Vue Router 解析 /conversations/:conversationId
→ 认证守卫 ensureSession
→ ConversationDetailView 创建
→ watch(route.params.conversationId, { immediate: true })
→ detailStore.load(conversationId)
→ Promise.all([
    getConversation(token, id),
    listConversationMessages(token, id)
  ])
→ FastAPI 验证 JWT 和会话所有权
→ PostgreSQL conversations / messages
→ Store 写入状态
→ Vue 自动重新渲染
```

`immediate: true` 的作用是：组件第一次创建就要加载；之后若 URL 从 A 变为 B，同一个组件实例也会再次加载 B。

## 4. 为什么两次 GET 并行

详情页需要两类互不依赖的数据：

```text
会话元数据：标题、模型、Token、归档状态
消息历史：角色、正文、状态、时间、错误信息
```

串行方式：

```text
等会话请求完成
→ 再请求消息
```

并行方式：

```text
同时发起两个请求
→ Promise.all 等待两个结果
```

后者的总等待时间接近较慢的那个请求，而不是两者相加。任一请求失败时，`Promise.all` 会进入 `catch`，页面不会把“标题成功但消息失败”的半成品伪装为完整详情。

## 5. 详情 Store 的状态

`ConversationDetailStore` 有：

| 字段 | 含义 |
|---|---|
| `conversationId` | 当前正在展示的会话 ID |
| `conversation` | 当前会话元数据；尚未成功加载时为 `null` |
| `messages` | 当前会话已持久化消息 |
| `isLoading` | 当前正在加载或刷新 |
| `hasLoaded` | 当前会话是否完成过一次完整读取 |
| `errorMessage` | 可显示给用户的中文错误 |
| `loadRequestVersion` | 内部请求版本号，不由页面直接使用 |

## 6. 为什么需要请求版本号

考虑用户网络较慢时快速切换：

```text
时刻 1：请求 A 开始
时刻 2：用户打开 B，请求 B 开始
时刻 3：B 返回并显示
时刻 4：A 才返回
```

如果 A 在时刻 4 直接写 Store，B 页面会突然出现 A 的标题和消息，这是严重的上下文错误。

当前规则：

```text
每次 load：loadRequestVersion + 1
响应返回：仅当响应版本 === 当前版本，才写入状态
```

因此 A 的旧响应被忽略。`reset()` 也会增加版本号，所以退出登录后尚未结束的请求同样不能写回旧用户数据。

这是一种轻量的“忽略过期结果”策略。未来在流式发送模块中，用户主动点击停止会额外使用 `AbortController` 终止网络流；两者解决的问题不同。

## 7. 页面四种状态

| 状态 | 页面表现 |
|---|---|
| 首次加载中 | “正在加载会话…” |
| 首次失败 | 中文错误和“返回会话列表” |
| 成功但无消息 | “还没有消息”空态 |
| 成功且有消息 | 按后端顺序的消息卡片 |

刷新同一个已经成功打开的会话时，页面保留旧消息并显示“正在刷新历史消息…”。如果刷新失败，旧内容仍在，同时显示错误；这和会话列表保留旧数据的设计一致。

切换到另一个会话时则先清空旧消息，因为 A 的内容绝不能在 B 页面短暂显示。

## 8. 历史消息如何显示

后端 `MessageResponse` 包含：

```text
role          user / assistant / system / tool
content       正文
status        pending / completed / failed / cancelled
error_message 安全的失败说明
created_at    ISO 时间
```

页面把 role 转换为中文标签：

```text
user      → 你
assistant → AI 助手
system    → 系统
tool      → 工具
```

非 `completed` 状态显示“生成中”“生成失败”或“已取消”。失败消息的 `error_message` 会额外展示；页面不显示内部堆栈或 Provider 密钥等敏感信息。

## 9. 退出登录为什么清两份 Store

详情页的退出逻辑为：

```text
detailStore.reset()
→ conversationStore.reset()
→ authStore.logout()
→ 跳转 /login
```

列表 Store 和详情 Store 分开存放不同数据，所以都要清。这样在同一标签页中用户 A 退出、用户 B 登录时，不会残留 A 的会话标题或历史消息。

## 10. 测试保护什么

Store 测试保护：

- 用 Token 并行读取详情和消息。
- 404 或网络错误显示错误。
- 刷新同一会话失败时保留旧数据。
- A、B 两次请求乱序返回时，旧 A 不覆盖 B。
- 没有 Token 时不访问接口且清空详情。

组件与路由测试保护：

- URL 参数触发实际加载。
- 标题、用户消息和助手消息都正确渲染。
- 空消息不是错误。
- 错误页面有返回列表入口。
- 路由参数变更重新加载新会话。
- 列表链接的 `href` 包含正确会话 ID。
- 已登录用户能够进入详情路由。

## 11. 本模块没有做什么

- 没有发送消息或接收 SSE。
- 没有取消生成。
- 没有重试失败消息。
- 没有会话重命名、归档或删除。
- 没有消息分页；第一版先读取该会话全部历史。
- 没有真实 Docker 演示账号的浏览器 E2E。

下一模块将在详情页加入聊天输入框，连接已有 `streamConversationMessage`。它会先把用户问题和 pending 助手消息显示出来，再按 SSE `delta` 逐段更新助手正文。

## 12. 面试表达

> 我把会话 ID 放进 Vue Router 的动态参数，详情 Store 并行读取会话元数据和历史消息。Store 区分首次加载、刷新和跨会话切换：同一会话刷新失败保留旧内容，而切到另一个会话先清空旧内容。为防止异步竞态，我用递增请求版本号保证只有最新请求能写状态。页面只展示已持久化的 MessageResponse，流式新消息会在下一模块单独接入。

## 13. 小练习

请解释为什么切换 A → B 时要清空 A 的消息，而刷新 A 失败时却保留 A 的旧消息。

答案要点：A → B 时旧内容属于不同业务对象，继续显示会造成上下文错位；刷新 A 时旧内容仍属于当前对象，而且来自一次成功读取，因此可作为暂时可用数据，但必须提示刷新失败。
