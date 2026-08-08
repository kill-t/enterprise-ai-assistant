# Vue 会话状态 Store

## 1. 本模块解决什么问题

第 28 模块已经能正确请求会话 CRUD 接口，但页面还需要管理：

- 正在加载还是正在创建。
- 上一次成功得到的会话列表。
- 请求失败时显示什么中文错误。
- 新建会话成功后怎样立即更新列表。
- 用户退出登录后怎样清理旧用户的内存数据。

这些都不属于 HTTP 协议，也不属于某一个 Vue 组件，因此放入 Pinia Store。

```text
ConversationList.vue（下一模块）
→ useConversationStore
→ conversations.ts
→ requestJson
→ FastAPI
→ PostgreSQL
```

## 2. Store 和 API Client 的边界

| 层 | 负责 | 不负责 |
|---|---|---|
| API Client | URL、方法、JSON、Bearer Token、HTTP 错误 | 列表状态、按钮加载状态、页面提示 |
| ConversationStore | 列表、加载、创建、错误、成功后的内存同步 | `fetch`、拼接 URL、解析 HTTP 响应 |
| Vue 页面 | 输入框、按钮、空态、渲染列表 | 业务状态持久化、直接请求后端 |

这种拆分让同一个 Store 可以同时被桌面布局、移动布局或将来的命令面板复用。

## 3. 状态字段

`useConversationStore` 维护：

```text
conversations     当前已读取到的会话列表
isLoading         正在读取列表
isCreating        正在创建会话
hasLoaded         是否成功读到过完整列表
includeArchived   当前列表是否包含归档会话
errorMessage      可以直接显示给用户的中文错误
```

`isLoading` 与 `isCreating` 分开，因为“刷新列表”和“创建会话”是不同请求。页面可以在创建期间禁用“新建”按钮，但不必遮住已经存在的列表。

## 4. 完整调用链

读取列表：

```text
页面调用 store.loadConversations()
→ Store 从 AuthStore 读取 Token
→ 有 Token：listConversations(token)
→ API Client 调用 FastAPI
→ 成功：替换 conversations，hasLoaded = true
→ 失败：保留原 conversations，只写 errorMessage
→ finally：isLoading = false
```

创建会话：

```text
页面调用 store.createConversation(input)
→ Store 从 AuthStore 读取 Token
→ API Client POST 创建请求
→ FastAPI 返回新 ConversationResponse
→ Store 将新会话插入数组首位
→ 页面可立刻显示它，无需再 GET 整个列表
```

这里的“插入首位”不是数据库写入；数据库已经由 FastAPI 完成写入。它只是把后端确认成功的结果同步到当前浏览器内存。

## 5. 为什么失败时保留旧列表

假设用户已经看到了 10 个会话，此时点击刷新却遇到暂时网络故障。

错误做法：

```text
开始请求
→ 立刻 conversations = []
→ 网络失败
→ 用户以为历史会话都消失了
```

当前做法：

```text
开始请求
→ 保留上一次成功 conversations
→ 网络失败
→ 显示 errorMessage
→ 用户仍能看到旧列表，并知道刷新没有成功
```

下一次成功加载时，后端返回的完整列表会替换旧数据。这是缓存展示状态，不是把前端内存当成事实来源；PostgreSQL 仍是最终事实来源。

## 6. 没有 Token 时为什么不请求

Store 每次操作前读取 `AuthStore.token`。

```text
没有 Token
→ 不发送 HTTP 请求
→ reset Store
→ 显示“登录状态已失效，请重新登录”
```

这样可以避免在退出登录后的旧组件中继续发请求，制造无意义的 401 日志。真正安全仍由后端 JWT 校验保证；这里是前端状态一致性保护。

## 7. 为什么退出登录要 reset

Pinia Store 运行在当前 Vue 应用内。用户 A 登出、用户 B 不刷新页面直接登录时，若不清空 Store，B 可能短暂看到 A 的会话列表。

当前 `HomeView.logout()` 的顺序是：

```text
conversationStore.reset()
→ authStore.logout()
→ router.replace(/login)
```

`reset()` 只清空浏览器内存，没有删除 PostgreSQL 数据。会话数据的真实删除只能通过受认证的 FastAPI DELETE 接口完成。

## 8. 测试保护什么

`conversations.test.ts` 测试：

- 加载列表时使用 AuthStore 的当前 Token。
- 成功加载后保存列表、筛选状态与 `hasLoaded`。
- 加载失败后旧列表不丢失，且展示后端中文错误。
- 没有 Token 时不调用 API。
- 创建成功后新会话排在列表最前。
- `reset()` 清除旧用户残留状态。

测试中的 API 函数被 mock，是因为本模块只验证 Store 的状态规则；第 28 模块已经单独验证 API Client 的 HTTP 协议。这不是独立于项目的测试代码，而是与生产 Store 一起由 Vitest 运行的项目测试。

## 9. 当前没有做什么

- 尚未渲染会话列表页面。
- 尚未提供新建、归档和删除按钮。
- 尚未加载某个会话的消息历史。
- 尚未把当前选中的会话 ID 放进 URL。
- 尚未处理多个并发刷新请求的“后发先至”竞争。

下一步会先做会话列表页面与“新建会话”按钮。归档、删除、消息历史和聊天输入会继续拆成更小的模块。

## 10. 面试表达

> 我把会话 API 协议和前端业务状态分开：API Client 只处理 HTTP，Pinia Store 处理列表、加载、创建和错误。刷新失败时我保留上次成功列表，避免临时断网造成界面看起来像数据丢失；创建成功后直接将后端返回对象插入首位，减少一次不必要的列表请求。退出登录会显式 reset Store，避免同一标签页切换账号时出现旧用户数据残留。

## 11. 小练习

请解释为什么下面两种错误处理不一样：

```text
首次加载失败：列表可以是空的 + 显示错误
已经加载成功后刷新失败：保留旧列表 + 显示错误
```

答案要点：首次没有任何已验证过的可展示数据；后者的旧列表来自一次成功请求，仍比“什么也不显示”更有用，但必须同时告诉用户最新刷新失败，不能把旧数据伪装成最新结果。
