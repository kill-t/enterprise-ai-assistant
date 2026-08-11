# Vue 知识库 API 与状态层

## 1. 本模块解决什么问题

后端已经提供知识库创建、查询、修改和软归档接口，但 Vue 页面此前没有对应 TypeScript 类型、请求函数
和状态容器。如果直接开始画页面，就会把下面这些逻辑全部写进组件：

- URL 和请求方法；
- Bearer Token；
- 加载、创建和归档状态；
- 请求失败后的中文提示；
- 成功后怎样更新当前列表；
- 同一个知识库如何防止重复操作。

本模块先建立页面下面的数据地基：

```text
后续 Vue 页面
→ useKnowledgeBaseStore
→ knowledge-bases API Client
→ requestJson
→ FastAPI /api/v1/knowledge-bases
```

本步骤没有增加页面或路由，所以浏览器暂时看不到新的知识库入口。

## 2. 三层分别负责什么

### TypeScript 契约

`contracts.ts` 描述前后端交换的数据形状：

```text
KnowledgeBaseResponse
KnowledgeBaseCreateInput
KnowledgeBaseUpdateInput
```

响应中包含后端冻结的 Embedding 配置快照，但创建请求不允许页面提交这些字段。也就是说，页面不能
伪造向量维度或模型名称，真正配置仍由后端统一决定。

### API Client

`knowledge-bases.ts` 只负责 HTTP 协议：

- 请求路径；
- GET、POST、PATCH、DELETE 方法；
- JSON 序列化；
- Token 传递；
- 解析响应。

API Client 不决定加载动画，也不修改 Vue 状态。

### Pinia Store

`knowledge-bases.ts` Store 负责页面状态和业务编排：

- 当前知识库列表；
- 是否正在加载或创建；
- 哪个知识库正在更新或归档；
- 全局错误和单项错误；
- 成功后局部替换或移除列表项。

Store 不拼接 URL，也不直接调用 `fetch`。

## 3. 知识库响应为什么包含 Embedding 快照

每个知识库返回：

```text
embedding_provider
embedding_model
embedding_dimension
```

这些字段说明当前知识库里的文档向量是怎样生成的。查询向量必须使用同一个模型和维度，否则不能安全
比较。它们是后端创建知识库时冻结的只读信息，不是普通用户随时可修改的表单字段。

前端保留这些字段，后续可以在知识库详情中用于调试和演示；创建与更新输入故意不允许修改它们。

## 4. `description` 的 undefined 和 null 有什么区别

更新请求中：

```text
没有 description 字段 → 不修改原描述
description: null       → 清空原描述
description: "新内容"   → 替换原描述
```

因此 `KnowledgeBaseUpdateInput` 必须写成：

```ts
description?: string | null
```

如果只写 `description?: string`，前端就无法表达“用户主动清空描述”。如果序列化时擅自删除 `null`，
后端收到的含义也会从“清空”错误变成“不修改”。API 测试专门保护了这个区别。

## 5. 为什么 DELETE 叫 archive

后端路由虽然使用：

```http
DELETE /api/v1/knowledge-bases/{id}
```

但业务实现是软归档：

```text
is_archived = true
```

它不会立刻物理删除文档、Chunk 或历史引用。前端函数因此命名为 `archiveKnowledgeBase`，避免开发者
误以为数据已经不可恢复。

归档接口返回 `204 No Content`，只确认操作成功，不返回新的 `updated_at`。Store 的处理规则是：

- 当前只看活动知识库：从列表移除目标。
- 当前包含归档知识库：保留目标，只把已确认的 `is_archived` 改为 `true`。
- 不伪造新的 `updated_at`。

如果后续页面必须立刻展示精确更新时间，可以重新加载列表，或者将后端接口改成返回完整更新对象。

## 6. 为什么加载失败不清空旧列表

Store 的加载顺序是：

```text
isLoading = true
→ 保留当前 knowledgeBases
→ 请求后端
→ 成功：替换列表并标记 hasLoaded
→ 失败：保留旧列表并写入 errorMessage
→ isLoading = false
```

网络短暂故障不代表旧数据突然不存在。保留最近一次成功结果，可以让页面显示“刷新失败”而不是整个
界面闪成空状态。

第一次加载失败时 `hasLoaded` 仍为 `false`，页面以后可以区分：

- 从未成功加载；
- 已有旧数据，但这次刷新失败。

## 7. 为什么创建成功后直接插入列表

创建接口已经返回完整 `KnowledgeBaseResponse`，包括后端生成的 ID、Embedding 快照和时间。因此 Store
可以直接执行：

```text
knowledgeBases.unshift(created)
```

不需要再发送一次完整列表请求。这减少一次网络往返，同时仍使用后端返回的真实对象，不是前端伪造
临时 ID。

## 8. 更新为什么等后端成功后再替换

本模块没有使用乐观更新：

```text
用户提交修改
→ 标记该知识库 updating
→ 请求 PATCH
→ 后端成功后替换对应列表项
```

如果先修改本地名称、后端再因重名返回 409，页面还需要回滚。第一版知识库操作频率低，等待后端确认
更容易理解，也能避免用户误以为修改已保存。

失败时：

- 原列表保持不变；
- 错误写入 `actionErrorMessages[knowledgeBaseId]`；
- 其他知识库不受影响。

## 9. 全局错误和单项错误为什么分开

状态中有两类错误：

```text
errorMessage
└─ 加载列表、创建知识库等页面级错误

actionErrorMessages[id]
└─ 某一条知识库的更新或归档错误
```

如果所有错误共用一个字符串，修改第二条知识库失败时，页面无法把提示放在对应卡片附近。按 ID 保存
错误让后续 UI 可以准确提示。

## 10. 怎样防止同一对象并行修改

Store 保存两个 ID 数组：

```text
updatingKnowledgeBaseIds
archivingKnowledgeBaseIds
```

Getter 将它们组合成：

```text
isManagingKnowledgeBase(id)
```

同一个知识库只要已有 PATCH 或 DELETE 未结束，新的管理请求就直接拒绝。否则可能发生：

```text
PATCH 改名先发出
DELETE 归档后发出
DELETE 先返回
PATCH 后返回并用旧状态覆盖归档结果
```

不同知识库仍可分别操作，不需要用一个全局 `isUpdating` 锁住整张列表。

## 11. Token 缺失时为什么清空状态

知识库属于用户私有数据。Store 发现没有 Token 时：

```text
$reset()
→ 清空上一个账号的知识库
→ 显示“登录状态已失效，请重新登录”
→ 不发送请求
```

这样可以避免退出或切换账号后，旧用户数据短暂留在前端内存中。

## 12. 关键代码入口

- TypeScript 数据契约：`frontend/src/api/contracts.ts`
- 知识库 API Client：`frontend/src/api/knowledge-bases.ts`
- API 协议测试：`frontend/src/api/knowledge-bases.test.ts`
- Pinia Store：`frontend/src/stores/knowledge-bases.ts`
- Store 行为测试：`frontend/src/stores/knowledge-bases.test.ts`
- 通用 HTTP Client：`frontend/src/api/client.ts`
- 后端路由：`backend/app/modules/knowledge/api/knowledge_base_router.py`
- 后端 Pydantic 契约：`backend/app/modules/knowledge/schema/knowledge_base_schema.py`

## 13. 测试与验收

API 测试覆盖：

- 默认列表与包含归档查询参数。
- Bearer Token。
- ID 路径编码。
- POST JSON 请求体与 201 响应。
- `description: null` 不丢失。
- DELETE 与 204 空响应。

Store 测试覆盖：

- 加载成功和筛选状态。
- 加载失败保留旧列表。
- 无 Token 时清除旧用户数据。
- 创建成功插入列表开头。
- 更新期间的单项状态和成功替换。
- 默认列表归档后移除目标。
- 包含归档列表只更新已确认字段。
- 同一对象更新与归档互斥。
- 单项失败保留列表并记录中文错误。
- reset 清除全部状态。

最终结果：

```text
前端：93 passed
TypeScript：vue-tsc 通过
生产构建：Vite build 通过
后端回归：323 passed
Ruff：通过
```

本模块没有数据库迁移，不需要启动 PostgreSQL、Redis、MinIO，也不需要模型或 Embedding 密钥。

## 14. 本模块尚未做什么

- 没有知识库列表页面。
- 没有创建或编辑表单。
- 没有归档确认对话框。
- 没有文档上传 API Client 和 Store。
- 没有会话知识库选择器。

下一小步是 **RAG-6B：Vue 知识库列表与创建界面**。它会新增受保护路由和知识库入口，使用本模块
Store 展示加载、空状态、错误状态和创建表单；文档上传继续放到之后的独立小步。

## 15. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 页面组件里到处出现 fetch | 缺少独立 API Client 和 Store |
| 清空描述后后端没变化 | 序列化时删除了 `description: null` |
| 点击“删除”以为数据物理消失 | 后端 DELETE 实际是软归档，应使用准确命名 |
| 刷新失败后列表突然变空 | 请求开始或 catch 中错误清空了旧数据 |
| 修改一条失败，所有卡片都显示错误 | 没有按知识库 ID 保存单项错误 |
| 两个按钮同时操作同一知识库 | 没有对象级处理中状态和互斥判断 |
| 创建后又请求一次完整列表 | 没使用创建接口返回的完整对象 |
| 退出后短暂显示旧账号知识库 | Token 缺失时没有 reset Store |

## 16. 面试表达

> 我没有直接在 Vue 页面里调用知识库接口，而是先拆出 TypeScript 契约、API Client 和 Pinia Store。
> API Client 只处理 HTTP，Store 处理列表、加载状态和成功后的局部更新。知识库 DELETE 在业务上是软
> 归档，所以前端使用 archive 命名；204 没有更新时间时只更新已确认的归档字段。更新和归档使用对象
> 级 ID 状态互斥，不会锁住整个列表，也避免并发响应覆盖。加载失败保留最近一次成功列表，单项操作
> 错误按 ID 保存，方便页面准确展示。

## 17. 小练习（约 15–20 分钟）

在 `frontend/src/stores/knowledge-bases.test.ts` 增加“创建失败”的测试：

1. 设置登录 Token。
2. 让 `createKnowledgeBase` 抛出 `ApiError("知识库名称已经存在", ...)`。
3. Store 中预先放一条知识库。
4. 调用 `store.createKnowledgeBase(...)`。
5. 断言返回 `null`。
6. 断言旧列表没有变化。
7. 断言 `errorMessage` 是后端中文消息，`isCreating` 恢复为 `false`。

运行：

```powershell
npm run test:run -- knowledge-bases.test.ts
```

答案要点：创建失败属于页面级操作，没有可用知识库 ID，因此错误放在 `errorMessage`，而不是
`actionErrorMessages`。
