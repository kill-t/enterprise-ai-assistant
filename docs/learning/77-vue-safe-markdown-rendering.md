# 模块 77：Vue 助手回答的安全 Markdown 渲染

## 1. 本模块解决什么问题

LLM 通常使用 Markdown 组织回答，例如标题、列表、代码块和表格。后端将原始 Markdown 保存到
PostgreSQL 是正确的，但旧页面使用普通 `<p>` 展示正文，因此用户会直接看到 `##`、`**` 和表格竖线。

本模块只改变展示层，不改变模型调用、SSE 协议和数据库结构：

```text
DeepSeek / Mock Provider 产生 Markdown
→ Conversation Service 保存原始 Markdown
→ SSE 或历史消息 API 返回同一份正文
→ ConversationDetailView 按消息角色选择组件
→ MessageMarkdownContent 解析并清理 Markdown
→ 浏览器显示标题、列表、代码和表格
```

## 2. 为什么数据库保存 Markdown，而不是 HTML

Markdown 是模型的原始业务输出，适合持久化、审计、导出和以后更换前端样式。HTML 属于具体页面的
展示结果，如果存进数据库，会让数据和某一版前端结构耦合，还会增加保存危险 HTML 的风险。

因此职责划分为：

- 后端保存原始 Markdown。
- 前端决定怎样渲染。
- CSS 决定最终视觉效果。
- 安全清理器决定哪些 HTML 标签和属性可以进入页面。

## 3. 为什么不能直接使用 `v-html`

`v-html` 会让浏览器把字符串作为 HTML 解析。如果模型回答、知识库片段或用户输入中混入脚本、事件
属性、危险链接，未经清理直接渲染可能产生 XSS。

当前采用两层处理：

```text
Marked：Markdown → HTML，只负责语法
DOMPurify：不可信 HTML → 白名单 HTML，负责安全边界
```

组件只允许回答展示需要的标签，例如标题、段落、列表、代码、链接和表格；图片、表单、脚本、内联
样式及事件属性均不开放。远程图片被禁止还可以避免回答偷偷加载追踪像素。

## 4. 真实代码调用链

### 4.1 会话页面选择渲染方式

入口：`frontend/src/views/ConversationDetailView.vue`

- `assistant` 消息交给 `MessageMarkdownContent`。
- `user`、`system` 和 `tool` 消息继续使用 Vue 文本插值。
- 文本插值会自动转义 HTML，因此用户输入不会被当成 Markdown 或 HTML 执行。

这是一个业务规则：只有系统生成的回答需要富文本展示。它放在会话页面，因为页面知道消息角色。

### 4.2 Markdown 组件完成解析和清理

入口：`frontend/src/components/MessageMarkdownContent.vue`

组件只有一个输入参数：

```ts
content: string
```

`sanitizedHtml` 是 Vue 计算属性。流式生成期间，Store 每追加一段正文，`content` 都会变化；计算属性会
自动重新执行，因此不需要给流式和历史消息分别设计两套渲染逻辑。

处理顺序不能颠倒：先让 Marked 生成 HTML，再让 DOMPurify 清理最终 HTML。如果先清理 Markdown，
Markdown 解析过程仍可能生成新的 HTML 节点，安全检查就没有覆盖真正进入页面的结果。

### 4.3 CSS 负责视觉效果

组件样式使用 Vue `scoped`，并通过 `:deep(...)` 选择 `v-html` 生成的内部节点。原因是这些标题、表格
并不是 Vue 模板编译阶段创建的，普通 scoped 选择器无法直接匹配。

表格设置了：

- 表头底色和字重。
- 单元格边框和间距。
- 偶数行背景色。
- 宽表格横向滚动。
- 不挤破消息卡片宽度。

## 5. 流式、完成和失败分别怎样执行

### 正常流式

```text
delta 到达
→ Store 更新助手消息 content
→ computed 重新解析和清理
→ 页面显示最新的 Markdown 结构
```

Markdown 尚未完整时可能暂时发生结构重排。例如只收到一半表格时先显示普通段落，分隔行到达后再
变为表格。这是增量 Markdown 的正常现象，不代表数据丢失。

### 页面刷新

历史 API 返回 PostgreSQL 中保存的原始 Markdown，同一个组件再次渲染，因此刷新前后展示一致。

### 失败和重试

失败消息仍可展示已经产生的部分 Markdown；原位重试时 Store 替换同一条消息内容，组件自动更新，
不需要直接操作 DOM。

## 6. 测试分别保护什么

`MessageMarkdownContent.test.ts` 是组件单元测试：

- 标题、列表、粗体、行内代码、引用和表格正确生成。
- `script`、远程图片、事件属性和危险链接不能进入可执行 DOM。
- props 变化时重新渲染，模拟流式追加。
- 空正文有明确占位文本。

`ConversationDetailView.test.ts` 是页面组件集成测试：

- 助手 Markdown 确实交给新组件。
- 用户输入中的 `**` 保持普通文字，不被错误渲染。

测试没有启动真实后端，也没有调用模型。它们在 jsdom 中独立验证前端组件行为；真实联调仍用于检查
浏览器布局、流式体验以及前后端协议能否共同工作。

## 7. 设计取舍与替代方案

### 让模型直接输出 HTML

实现看起来简单，但输出更难约束、审计和跨端复用，安全面也更大，因此不采用。

### 手写 Markdown 解析器

不增加依赖，但表格、嵌套列表、转义和代码块边界容易出错，不适合作品中的通用聊天界面。

### 服务端生成 HTML

可统一多个客户端的结果，但会把纯展示职责放进后端；当前只有 Vue 客户端，没有必要增加协议复杂度。

### 流式期间显示纯文本，完成后再渲染

视觉结构更稳定，但用户直到结束前都会看到 Markdown 符号。当前选择实时重新渲染，后续如果大型表格
频繁抖动，可以在前端加入节流，而不修改后端。

## 8. 常见错误

- 直接把 Marked 结果交给 `v-html`，忘记 Marked 不负责 XSS 清理。
- 为了显示一种格式，随意扩大 DOMPurify 标签或属性白名单。
- 对所有角色使用 Markdown，让用户输入变成可控富文本。
- 将渲染后的 HTML 保存进数据库，混淆业务事实与页面表现。
- 只测“文字能出现”，不检查脚本、事件属性和危险 URL。
- 使用 scoped CSS，却忘记 `v-html` 内部节点需要 `:deep`。

## 9. 面试表达

可以这样介绍：

> 模型输出以原始 Markdown 持久化，保持审计和多端复用能力。Vue 展示层使用 Marked 支持 GFM 表格，
> 再通过 DOMPurify 严格白名单清理后交给 `v-html`。只有 assistant 角色进入富文本链路，用户输入保持
> 文本转义。流式和历史消息复用同一个响应式组件，并用组件测试覆盖 Markdown 结构和 XSS 边界。

## 10. 小练习（约 15～30 分钟）

为安全测试再增加两个恶意样例：

1. `<iframe src="https://example.com"></iframe>` 不得出现在结果中。
2. `<a style="color:red" href="https://example.com">链接</a>` 可以保留链接，但必须删除 `style`。

要求只补测试，先观察当前实现是否已经满足；如果测试失败，再判断应该修改白名单还是组件逻辑。

答案要点：

- `iframe` 不在 `ALLOWED_MARKDOWN_TAGS`，应被删除。
- `style` 不在 `ALLOWED_ATTR`，应被删除。
- `href` 在允许属性中，并且 HTTPS URL 不危险，所以链接可以保留。
- 不应为了让测试通过而开放 `iframe` 或 `style`。
