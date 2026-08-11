# Vue 文档 API、状态层与可靠轮询

## 1. 本模块解决什么问题

上传文档不是一个“请求结束就全部完成”的操作。

后端收到文件后只完成两件事：

1. 把原文件保存到 MinIO。
2. 在 PostgreSQL 创建文档和入库任务。

真正的解析、切块和向量化由 Celery Worker 异步执行。因此上传接口返回 `202 Accepted`
时，文档通常仍是 `pending`，并不能立刻用于 RAG 检索。

前端必须管理下面这段生命周期：

```text
选择文件
→ 上传成功
→ pending
→ processing
→ ready 或 failed
```

本模块建立了文档 API Client 和 Pinia Store，让后续页面只负责展示状态和接收点击。

## 2. 它在整体架构中的位置

```text
Vue 文档页面（下一模块）
        ↓
Document Store
        ↓
Document API Client
        ↓
统一 requestJson / ApiError
        ↓
FastAPI Document Router
        ↓
Document Service / Repository / MinIO / Celery
```

各层职责：

- 页面：选择文件、显示列表、按钮和错误。
- Store：管理列表、加载状态、轮询、重试和删除恢复。
- API Client：决定 URL、HTTP 方法、Token 和请求体格式。
- 通用 Client：统一处理认证头、网络异常和业务错误。
- 后端：执行权限检查、事务、对象存储和任务投递。

页面不应该直接调用 `fetch`。否则轮询、错误提示和并发控制会散落在多个组件中。

## 3. TypeScript 契约

入口：`frontend/src/api/contracts.ts`

核心类型包括：

```ts
type DocumentStatus =
  | "pending"
  | "processing"
  | "ready"
  | "failed"
  | "deleted";
```

含义如下：

- `pending`：任务已经持久化，等待 Worker。
- `processing`：Worker 已领取任务，正在解析或向量化。
- `ready`：Chunk 和向量已经原子写入，可以检索。
- `failed`：本次任务失败，可以人工重试。
- `deleted`：后端删除过程使用的墓碑状态，正常列表不会返回。

`DocumentResponse` 保存页面和调试需要的信息：

- 文件名、类型、大小和 SHA-256。
- 当前业务状态。
- Parser 与 Chunker 版本。
- 页数和 Chunk 数。
- 错误码和错误消息。
- 创建、更新和完成时间。

上传和重试还会返回 `job_id`。文档与任务不是同一个概念：一个文档重试多次，可以对应多条任务记录。

## 4. API Client 如何工作

入口：`frontend/src/api/documents.ts`

提供五个函数：

```text
listDocuments  → GET    /knowledge-bases/{id}/documents
getDocument    → GET    /documents/{id}
uploadDocument → POST   /knowledge-bases/{id}/documents
retryDocument  → POST   /documents/{id}/retry
deleteDocument → DELETE /documents/{id}
```

所有 ID 都经过 `encodeURIComponent`。它不会代替后端 UUID 校验，但能防止空格、斜杠等字符
意外改变 URL 结构。

### 4.1 为什么上传使用 FormData

文件不是 JSON 字符串，而是二进制内容，因此使用浏览器的 `FormData`：

```ts
const formData = new FormData();
formData.append("file", file);
```

这里不能手工写：

```text
Content-Type: multipart/form-data
```

浏览器需要自动生成类似下面的完整请求头：

```text
Content-Type: multipart/form-data; boundary=----BrowserBoundary123
```

正文中的每一部分都依靠这个 `boundary` 分隔。手工设置不带 boundary 的请求头，FastAPI
可能无法识别文件。

## 5. Store 为什么按知识库保存列表

入口：`frontend/src/stores/documents.ts`

核心状态是：

```ts
documentsByKnowledgeBase: Record<string, DocumentResponse[]>
```

它相当于：

```text
知识库 A → [文档 1, 文档 2]
知识库 B → [文档 3]
```

这样切换知识库时，不需要把所有文档混在一个数组里再反复过滤，也不会把 A 的错误状态显示到 B。

加载、上传和轮询状态也按知识库 ID 保存；重试和删除状态按文档 ID 保存。这个粒度与实际操作对象一致。

## 6. 轮询的数据流

上传成功后 Store 已拿到一条 `pending` 文档，随后开始轮询：

```text
uploadDocument
→ 把 pending 文档写入 Store
→ startPolling
→ 等待 2 秒
→ pollKnowledgeBase
→ GET 文档列表
→ 替换为后端最新状态
→ 仍有 pending/processing：安排下一轮
→ 全部 ready/failed：停止轮询
```

第一版使用轮询，而不是 WebSocket 或 SSE，原因是：

- 文档状态变化频率低，2 秒一次足够演示。
- 后端已经有查询接口，不需要再维护长连接协议。
- 实现和恢复逻辑更容易讲清楚。

替代方案：

- SSE：服务器可以主动推送，单向事件很合适，但要新增 Worker 到 API 的事件转发链路。
- WebSocket：支持双向通信，但文档状态不需要高频双向交互，复杂度偏高。
- 固定 `setInterval`：代码简单，但慢请求可能与下一次请求重叠。

当前采用递归 `setTimeout`：上一轮请求结束后才安排下一轮，所以同一知识库不会堆积多个轮询请求。

## 7. 为什么计时器不放进 Pinia State

Pinia State 应主要保存可序列化的业务状态，例如文档列表和错误消息。

计时器句柄是浏览器运行资源：

- 浏览器中通常是数字。
- Node 测试环境中可能是对象。
- 它不应该被持久化或展示。

代码使用 `WeakMap<Store, Map<knowledgeBaseId, timer>>` 私下保存计时器。不同 Pinia 实例不会共享
计时器，Store 被回收后 WeakMap 也不会单独阻止垃圾回收。

`reset()` 会先执行 `stopAllPolling()`，再清空业务状态。这样退出登录或切换账号后不会继续用旧 Token 请求旧数据。

## 8. 正常、失败和超时如何执行

### 8.1 正常完成

```text
pending → processing → ready
```

Store 每轮替换为后端最新数据。看到列表里不再存在 `pending/processing` 后清除计时器。

### 8.2 入库业务失败

```text
pending → processing → failed
```

`failed` 也是终态，所以轮询停止。页面以后会展示 `error_message` 和“重试”按钮。

用户点击重试后，后端创建新任务并把文档恢复为 `pending`，Store 再次启动轮询。

### 8.3 一次轮询网络失败

Store 不清空旧列表，也不把文档改成 `failed`，因为网络失败不等于后台入库失败。

它会：

1. 保留最后一次已知状态。
2. 保存中文错误供页面提示。
3. 下一周期继续查询。
4. 成功恢复后清除旧错误。

### 8.4 离开页面或退出登录

页面下一模块会在不再观察某知识库时调用 `stopPolling`。退出登录走统一的
`resetUserDataStores`，Document Store 会停止所有计时器并清空列表。

## 9. 删除为什么不能乐观更新

乐观删除是指：用户点击后，前端先把文档从列表移除，再等待服务器响应。

这里没有使用它，因为删除横跨 PostgreSQL 和 MinIO：

```text
数据库写 DELETED 墓碑
→ 删除 MinIO 对象
→ 数据库物理清理
```

MinIO 故障时，后端会保留墓碑并返回失败。前端必须保留文档 ID，用户才能再次调用相同
DELETE 接口继续清理。

Store 使用 `deletionRecoveryDocumentIds` 记录这类文档。即使后端列表已经隐藏墓碑，下一次
列表刷新也会把内存中的恢复项保留下来。

### 9.1 响应丢失的特殊情况

可能出现：

```text
后端删除成功并返回 204
→ 网络中断
→ 浏览器只看到请求失败
```

用户再次删除时，后端因为记录已经不存在而返回 404。只有文档已经带有恢复标记时，Store 才把
这个 404 解释为“目标已经清理”，并移除本地残留。

首次删除直接得到 404 仍然按错误处理，不能把任意越权或错误 ID 伪装成成功。

## 10. 并发控制

Store 使用 ID 数组记录进行中的操作：

- 同一知识库一次只上传一个文件。
- 同一个文档的重试和删除互斥。
- 重复点击时不再发送第二个请求。

前端控制主要改善交互和减少无效请求，真正的一致性仍由后端条件更新、唯一索引和事务保证。
不能把按钮禁用当作安全边界，因为调用者可以绕过页面直接请求 API。

## 11. 测试保护了什么

API 测试覆盖：

- URL ID 编码。
- Bearer Token。
- FormData 文件字段。
- 不手工设置 multipart Content-Type。
- 重试 POST 的任务响应。
- 删除 204 空正文。

Store 测试覆盖：

- 按知识库加载和保留旧列表。
- 上传后插入 `pending` 文档。
- 轮询终态自动停止。
- 临时网络故障后继续轮询并恢复。
- 重试成功和失败。
- 删除响应前不提前移除。
- 删除失败后保留恢复身份。
- 恢复请求的 404 幂等处理。
- reset 停止计时器。
- 缺少 Token 时清除旧账号数据。
- 路由守卫会实际清理 Document Store。

完整回归在并行模式下曾有三个旧页面测试超过 5 秒，但没有断言错误。单独重跑全部通过，随后使用
`--maxWorkers=1` 完成 124 项全量测试。这说明是 Windows 本机同时启动多个 jsdom 环境造成的资源竞争，
而不是本模块功能回归。

## 12. 业务规则与基础设施代码

业务规则：

- `pending/processing` 需要继续观察。
- `ready/failed` 是本轮处理终态。
- 失败重试后重新进入 `pending`。
- 删除未确认前保留文档身份。

基础设施代码：

- URL 编码。
- FormData。
- Bearer Token。
- `setTimeout` 和 WeakMap。
- 网络错误转换。

区分二者有助于以后把轮询替换成 SSE：业务状态规则可以保留，只替换状态获取基础设施。

## 13. 常见错误

### 错误一：上传 202 就显示“入库完成”

202 只表示请求已接收，应显示“等待处理”。

### 错误二：轮询失败就把文档标成 failed

网络故障与 Worker 业务失败不是同一件事。业务状态只能相信后端持久化结果。

### 错误三：使用 setInterval 且不清理

可能产生重叠请求，并在退出登录后继续访问旧账号数据。

### 错误四：手工设置 multipart Content-Type

缺失 boundary 后服务端可能无法解析文件。

### 错误五：删除按钮一点击就从列表移除

对象存储故障时会丢失继续清理需要的文档 ID。

## 14. 面试时可以怎么讲

> 文档入库是异步工作流，上传接口返回 202 后前端不能把它视为完成。我把 HTTP 协议封装在
> Document API Client，把文档列表、操作互斥和轮询生命周期放在 Pinia Store。轮询采用请求完成后
> 再递归 setTimeout 的方式，避免慢请求重叠，并在全部文档进入终态、页面离开或账号重置时清理。
> 删除没有采用普通乐观更新，因为后端需要用墓碑协调 PostgreSQL 与 MinIO；失败时前端保留恢复 ID，
> 还处理了成功响应丢失后再次删除得到 404 的幂等场景。

## 15. 小练习

练习时间约 15～30 分钟。

请先不要修改 Store，回答下面三个问题：

1. 为什么 `pending` 文档需要轮询，而 `failed` 文档不需要自动轮询？
2. 如果把轮询改成 `setInterval`，当一次请求耗时 5 秒、间隔是 2 秒时可能发生什么？
3. 为什么删除失败后保留的是文档 ID，而不是只保留一条错误文字？

然后尝试补一个测试：同一个文档正在执行重试时，再调用删除，应返回 `false`，并且不能请求删除 API。

答案要点：

- `pending` 还会由后台继续变化；`failed` 已是持久化终态，需要用户明确重试。
- 固定间隔会在旧请求未结束时发出新请求，造成重叠和响应乱序。
- 错误文字只能展示，文档 ID 才能再次定位同一个后端资源完成恢复操作。

## 16. 下一步

下一模块将实现 Vue 文档界面：

- 在知识库卡片中打开文档管理区域。
- 选择 PDF、TXT、Markdown 文件并上传。
- 展示文件大小、状态、页数、Chunk 数和错误。
- 对失败文档提供重试按钮。
- 对终态文档提供带确认的删除按钮。
- 页面卸载或切换知识库时停止对应轮询。

本模块没有改数据库，也不需要 Embedding 模型密钥。
