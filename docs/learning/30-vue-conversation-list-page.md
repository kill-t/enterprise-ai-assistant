# Vue 会话列表页面与新建会话

## 1. 本模块解决什么问题

前两个前端模块已经有：

- 会话 HTTP API Client。
- Pinia 会话状态 Store。

但用户仍看不到自己的会话，也不能创建新会话。本模块把它们接入受保护工作台：

```text
访问 /
→ Router 守卫确认登录
→ HomeView 挂载
→ ConversationStore.loadConversations()
→ GET /api/v1/conversations
→ 显示列表、空态或错误

点击“新建会话”
→ ConversationStore.createConversation({ title: "新建会话" })
→ POST /api/v1/conversations
→ Store 将后端返回会话插入列表首位
→ 页面立即渲染新卡片
```

第一版只呈现会话卡片，没有让卡片跳转。原因是消息详情路由和消息历史还没有实现；不可点击的卡片比“点了却没有动作”的假入口更诚实。

## 2. 页面、Store 与 API 的职责

| 层 | 本模块中的职责 |
|---|---|
| `HomeView.vue` | 生命周期、按钮点击、加载态、空态、错误态、会话卡片渲染 |
| `ConversationStore` | 请求列表和创建会话、保存列表、管理 `isLoading` 与 `isCreating` |
| `conversations.ts` | 拼接 HTTP 协议，调用公共 `requestJson` |
| FastAPI | 验证 JWT、所有权过滤、写入 PostgreSQL |

页面没有直接调用 `fetch`，也没有自行修改 Token。这使同一个 Store 能在以后被侧边栏、移动页面或命令面板复用。

## 3. 为什么挂载时只读取一次

`HomeView` 的逻辑是：

```ts
onMounted(() => {
  if (!conversationStore.hasLoaded) {
    void reloadConversations();
  }
});
```

首次进入工作台需要请求后端；同一会话内从未来页面返回工作台时，如果 Store 已有一次成功列表，不再无意义地重复请求。

这里的 `void` 有明确含义：生命周期钩子不等待这个 Promise 来渲染页面，页面先呈现“正在加载会话”，完成后 Pinia 状态变化会自动触发重新渲染。

如果用户点击“重新加载”，则不检查 `hasLoaded`，而是主动再次请求。

## 4. 四种页面状态

| 条件 | 显示内容 |
|---|---|
| 首次请求中 | “正在加载会话…” |
| 请求成功且列表为空 | 空态和“新建会话”入口 |
| 请求成功且有数据 | 会话卡片列表 |
| 请求失败 | 中文错误和“重新加载”按钮 |

刷新失败但已有旧列表时，Store 会保留旧列表，页面同时显示错误。这样用户不会误以为会话被删除，但也不会把旧数据误认为刚刷新成功。

## 5. 为什么创建过程中禁用按钮

创建请求尚未完成时：

```text
isCreating = true
→ 按钮文字：正在创建…
→ disabled = true
```

这不是为了“看起来在加载”，而是防止用户连续点击造成多次 POST 请求和多个重复会话。请求无论成功或失败，Store 的 `finally` 都会恢复 `isCreating = false`。

## 6. 新建会话为什么使用默认标题

后端创建会话要求非空 `title`，而本模块还没有标题输入框或重命名界面。因此先约定默认值：

```text
新建会话
```

这让用户能先进入聊天，再在后续“会话管理”模块实现重命名。默认标题只是第一版交互选择，不代表数据库的默认值；仍由前端显式提交，后端继续进行非空校验。

## 7. 时间格式为什么属于页面

后端返回机器友好的 ISO 字符串，例如：

```text
2026-08-08T00:00:00Z
```

`formatUpdatedAt` 在页面转换为中文可读时间，同时把原始时间保留在：

```html
<time datetime="原始 ISO 值">显示文本</time>
```

API DTO 必须稳定、可机器解析；“显示成什么日期格式”是界面需求，因此不应该放进 API Client 或数据库。

## 8. 测试保护什么

`HomeView.test.ts` 使用真实 Pinia Store 和 Router，但 mock 网络 API：

- 挂载后自动读取会话并渲染卡片。
- 空列表显示空态。
- 创建请求未完成时按钮禁用并显示“正在创建…”。
- 创建成功后立刻渲染返回会话，且不再额外 GET 整个列表。
- 首次加载失败时显示错误；点击重试后能渲染成功结果。

这不是独立于项目的示例代码。它和生产页面一起位于 `frontend/src`，由 `npm run test:run` 运行。

## 9. 本模块没有做什么

- 会话卡片跳转到详情页。
- 会话消息历史与 SSE 聊天界面。
- 会话重命名、归档、删除。
- 已归档会话筛选。
- 并发刷新请求的版本控制。
- 用真实 Docker 演示账号完成浏览器登录 E2E。

下一步将做“当前会话路由与消息历史读取”：把会话 ID 放进 URL，并从 FastAPI 读取该会话已经保存的消息。这样才能在其上接入流式发送。

## 10. 面试表达

> 我把会话列表页面作为 API Client 和 Pinia Store 的薄 UI 层：组件挂载时根据 hasLoaded 决定是否读取，Store 管理加载和错误，API Client 负责 HTTP。列表刷新失败时保留上次成功结果，创建成功后直接使用后端返回对象更新内存，避免额外列表请求。我还会在创建期间禁用按钮，防止重复 POST；退出登录时 reset Store，防止不同用户共享同一标签页残留的数据。

## 11. 小练习

请回答：为什么 `formatUpdatedAt` 放在 `HomeView`，而不是放在 `conversations.ts`？

答案要点：`conversations.ts` 是 HTTP 协议层，应该保留稳定、机器可读的 ISO 数据；日期语言、时区、显示精度属于具体页面体验。未来不同页面可以用不同格式，而无需修改 API Client。
