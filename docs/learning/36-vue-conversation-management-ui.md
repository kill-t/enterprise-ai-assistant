# Vue 会话管理界面

## 1. 本模块解决什么问题

上一模块已经让 Store 能够重命名、归档和删除会话，但用户还没有入口操作它。本模块在工作台列表加入：

- 重命名表单；
- 归档按钮；
- 删除二次确认；
- “显示已归档”筛选；
- 每个会话独立的处理中状态与错误提示。

它是页面层，职责是让用户完成交互；HTTP 请求、状态更新和失败恢复仍在 `ConversationStore`。

## 2. 真实调用链

```text
HomeView 管理按钮
→ 打开重命名框 / 删除确认框，或调用归档动作
→ ConversationStore
→ conversations API Client
→ FastAPI PATCH / DELETE
→ PostgreSQL
→ Store 更新 conversations
→ Vue 响应式重新渲染列表
```

例如删除并不是：

```text
点击“删除” → DELETE 请求
```

而是：

```text
点击“删除” → 仅记录 deleteTargetConversation、显示确认框
→ 点击“确认删除” → Store.deleteConversation(id)
→ 后端成功 → 关闭确认框、列表移除该项
```

这保证用户第一次点击不会产生不可恢复的副作用。

## 3. 页面本地状态与 Store 状态

`HomeView` 的本地 `ref` 只保存页面暂态：

```ts
renameConversationId       // 当前正在编辑哪一条
renameTitle                // 输入框草稿
deleteTargetConversation   // 当前等待确认删除的对象
```

这些内容不需要跨页面保存，也不应该放入全局 Store。

`ConversationStore` 保存可共享、与后端请求相关的状态：

```ts
updatingConversationIds
deletingConversationIds
actionErrorMessages
```

页面通过 `computed` 读取当前对话框所对应 ID 的状态。请求未结束时，保存、取消和遮罩点击都会禁用或忽略，避免用户误判“关闭窗口等于取消服务端请求”。

## 4. 为什么不能把按钮放进 RouterLink

会话卡片本身是跳转详情的链接。重命名、归档、删除是按钮，不能嵌套在链接内部：

```html
<!-- 不正确：交互元素嵌套，点击含义不清晰 -->
<a>
  <button>删除</button>
</a>
```

当前结构把它们放在同一个 `article` 下：

```text
article
├─ RouterLink：进入会话详情
└─ buttons：管理会话
```

这样既符合语义，也避免点击删除时意外跳转到详情页。

## 5. 筛选为什么重新请求后端

“显示已归档”调用：

```ts
conversationStore.loadConversations({ includeArchived: true })
```

默认状态则不传该参数，使用后端的默认“未归档”规则。不能只对浏览器当前已有数组做 `filter`，因为：

- 当前数组默认根本没有已归档数据；
- 数据来源与用户所有权过滤必须由服务端保证；
- 重新进入页面或刷新后，结果应与服务器一致。

## 6. 失败和成功时的界面行为

| 操作 | 请求成功 | 请求失败 |
| --- | --- | --- |
| 重命名 | 关闭对话框，卡片标题更新 | 对话框保留，展示错误，原标题不变 |
| 归档 | 默认列表移除该项；“包含归档”列表更新其标记 | 列表保留，在该会话下显示错误 |
| 删除 | 关闭确认框，列表移除该项 | 确认框保留，展示错误，列表不变 |

页面不会自行伪造成功状态；所有成功判断都来自 Store 返回的后端结果。

## 7. 测试保护什么

`frontend/src/views/HomeView.test.ts` 是组件测试，使用真实 Pinia Store，但 mock API Client：

- 打开重命名框、提交表单、页面显示后端返回的新标题；
- 点击归档后默认列表变为空；
- 第一次点击删除只打开确认框，确认后才发送 DELETE；
- 切换筛选会以 `includeArchived: true` 重新读取列表，并显示“已归档”标记。

这里不访问真实 FastAPI 或 PostgreSQL。上一模块的 Store 单元测试负责请求状态规则；后端测试负责权限和数据库行为；本模块确认“用户点击后的页面连接是否正确”。

组件测试里要触发表单提交，应触发 `form` 的 `submit` 事件。单独触发按钮的 `click` 在测试环境中不一定执行浏览器原生的表单默认提交行为。

## 8. 本模块没有做什么

- 没有“取消归档”或回收站；删除仍不可恢复。
- 没有批量归档、批量删除。
- 对话框当前提供 `role="dialog"`、`aria-modal` 和重命名输入框自动聚焦，但未实现完整的 Tab 焦点循环与 Escape 快捷键；后续可抽取可复用 Dialog 组件时补齐。
- 未做后端分页；会话量很大时应添加 cursor 分页与搜索。

## 9. 面试表达

> 我把会话管理拆成页面交互状态和 Store 请求状态：页面只保存正在编辑或确认的对象，Store 按会话 ID 保存请求中和错误状态。删除需要二次确认，且只在后端 DELETE 成功后移除列表。归档筛选重新请求服务端，而不是前端临时过滤，避免数据范围和权限边界不一致。

## 10. 小练习

请为删除确认框补一个组件测试：点击“取消”后确认框消失，且 `deleteConversation` 从未被调用。

答案要点：挂载并加载一条会话，点击删除按钮打开 `delete-dialog`，点击“取消”，断言 `delete-dialog` 不存在，断言 mock 的 `deleteConversation` 调用次数仍为 0。
