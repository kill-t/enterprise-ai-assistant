# Vue 知识库列表与创建界面

## 1. 本模块解决什么问题

上一模块已经有知识库 API Client 和 Pinia Store，但用户还不能在浏览器中操作。本模块将数据层接入
真实 Vue 页面：

```text
工作台“管理知识库”入口
→ /knowledge-bases 受保护路由
→ KnowledgeBasesView
→ useKnowledgeBaseStore
→ knowledge-bases API Client
→ FastAPI
```

当前页面支持：

- 查看当前用户知识库。
- 查看活动或包含归档的列表。
- 创建知识库。
- 展示加载、空、错误和成功状态。
- 展示冻结的 Embedding Provider、模型和向量维度。

本步骤暂不加入编辑、归档按钮和文档上传。

## 2. 为什么不把所有知识库功能一次写完

知识库页面最终还需要：

```text
创建知识库
→ 编辑或归档
→ 上传文档
→ 查看入库状态
→ 失败重试
→ 会话选择知识库
```

如果一次完成，页面会同时出现表单、确认框、文件上传、轮询和多种错误状态，很难分清每段代码属于
哪个业务过程。本模块只打通“列表 + 创建”，下一模块再复用既有 Store 完成编辑和软归档。

## 3. 页面状态来自哪里

页面同时使用两类状态。

### Pinia 中的服务器状态

这些状态与后端数据或网络请求有关：

```text
knowledgeBases
isLoading
isCreating
hasLoaded
includeArchived
errorMessage
```

切换路由后仍可能复用，适合放在 Store。

### 组件中的表单状态

这些状态只属于当前创建表单：

```text
name
description
formErrorMessage
createSuccessMessage
```

离开页面后无需保留，适合使用 `ref` 放在组件内。如果把每次输入的半成品也放入全局 Store，会增加
状态清理和多页面冲突的复杂度。

## 4. 创建请求的数据流

```text
用户填写名称与描述
→ submitKnowledgeBase
→ trim 两端空格
→ 前端长度校验
→ Store.createKnowledgeBase
→ API Client POST JSON
→ FastAPI 再次校验与写库
→ 返回完整 KnowledgeBaseResponse
→ Store 插入列表开头
→ 页面清空输入并显示成功提示
```

前端校验用于快速反馈，不能替代后端校验。请求可以绕过 Vue 直接访问 API，所以唯一性、所有权和字段
长度必须继续由后端保护。

## 5. 为什么空描述传 null

创建时将纯空格描述归一化为：

```json
{"description": null}
```

数据库因此保存空值，而不是没有业务含义的空字符串。页面展示时使用“暂无描述”作为视觉兜底，但
不会把这四个字写回数据库。

## 6. 为什么创建错误放在表单旁边

列表加载错误和创建错误的操作位置不同：

- 列表加载失败：在列表区域提示，并提供“重新加载”。
- 创建失败：在创建按钮附近提示，用户可以直接修改输入。

Store 当前使用页面级 `errorMessage` 接收后端创建错误。组件读取后把它移动到本地
`formErrorMessage`，然后调用 `clearError()`，避免同一条错误同时在表单和列表区域重复显示。

以后如果创建流程更复杂，也可以在 Store 中拆出独立 `createErrorMessage`。第一版保持状态数量较少，
同时通过页面逻辑明确错误归属。

## 7. 四种列表状态怎样区分

页面判断顺序如下：

```text
isLoading && !hasLoaded
→ 首次加载

errorMessage
→ 加载失败，可重试

hasLoaded && knowledgeBases.length === 0
→ 成功加载但没有数据

knowledgeBases.length > 0
→ 展示列表
```

`hasLoaded` 很重要。数组为空既可能是“请求还没完成”，也可能是“后端确认没有知识库”，不能只根据
`length === 0` 决定显示空状态。

## 8. 为什么显示 Embedding 配置

每张知识库卡片显示：

- Provider。
- Embedding 模型。
- 向量维度。

这些不是普通用户必须理解的字段，但对当前求职演示项目很有价值：它能直接说明文档向量和查询向量
必须使用同一配置，知识库在创建时已经冻结配置快照。

页面只展示这些字段，不提供修改表单，避免文档已经入库后被前端随意切换维度。

## 9. 受保护路由怎样工作

新增路由：

```text
GET /knowledge-bases
name: knowledge-bases
meta.requiresAuth: true
```

导航前，路由守卫执行：

```text
authStore.ensureSession()
→ 有有效 Token：进入页面
→ 没有 Token：清理用户私有 Store
→ 跳转 /login?redirect=/knowledge-bases
```

隐藏页面不等于清理数据。Pinia 位于浏览器内存中，如果只跳登录，上一位用户的知识库仍可能在下一次
挂载时短暂出现。因此路由拒绝访问时还要调用统一清理函数。

## 10. 为什么增加统一用户数据清理函数

`resetUserDataStores()` 集中清理：

```text
Conversation Detail Store
Conversation List Store
Knowledge Base Store
```

工作台退出、会话详情退出、知识库页面退出以及路由守卫都复用这个函数。这样以后新增私有 Store 时，
只需要在一个入口补充，不必寻找所有退出按钮。

认证 Store 仍只管理 Token 和用户身份；业务 Store 清理由协调函数负责，避免 Auth Store 与多个业务
Store 形成循环依赖。

## 11. 为什么列表支持包含归档

默认请求不传查询参数，由后端只返回活动知识库。用户点击“显示已归档”后：

```text
includeArchived = true
→ GET /api/v1/knowledge-bases?include_archived=true
```

归档对象显示明确标签。当前没有恢复和归档按钮，下一模块会在已有 Store 更新、归档能力上补 UI。

## 12. 响应式布局

桌面端使用两列：

```text
创建表单 | 知识库列表
```

屏幕小于 900px 时变为单列；手机端元数据从三列变为一列，工作台的“管理知识库”和“新建会话”按钮
也会纵向排列。这一变化只由 CSS 控制，不复制第二套移动端组件。

## 13. 关键代码入口

- 知识库页面：`frontend/src/views/KnowledgeBasesView.vue`
- 页面交互测试：`frontend/src/views/KnowledgeBasesView.test.ts`
- 路由配置：`frontend/src/router/index.ts`
- 路由守卫测试：`frontend/src/router/index.test.ts`
- 工作台入口：`frontend/src/views/HomeView.vue`
- 用户数据清理：`frontend/src/stores/user-data.ts`
- 知识库 Store：`frontend/src/stores/knowledge-bases.ts`
- 响应式样式：`frontend/src/style.css`

## 14. 测试保护了什么

页面测试覆盖：

- 挂载后使用当前 Token 加载知识库。
- 文件描述、Embedding 模型和维度正确展示。
- 空列表显示业务空状态。
- 名称为空和描述超长时不发送请求。
- 创建期间禁用输入与按钮。
- 创建成功后清空表单并立即更新列表。
- 创建失败保留旧列表并显示后端中文错误。
- 切换包含归档列表并展示归档标签。
- 首次加载失败后能够重试。

路由测试覆盖：

- 有效 Token 可以进入知识库页。
- 路由带有 `requiresAuth`。
- 无 Token 时跳转登录并清空旧知识库状态。
- 工作台入口生成正确 `/knowledge-bases` 链接。

最终验证：

```text
前端：101 passed
TypeScript：vue-tsc 通过
生产构建：Vite build 通过
后端回归：323 passed
Ruff：通过
```

本模块没有数据库迁移，不需要启动 PostgreSQL、Redis、MinIO，也不需要模型密钥。

## 15. 本模块尚未做什么

- 不能编辑知识库名称和描述。
- 不能归档或恢复知识库。
- 不能上传或查看文档。
- 不能查看入库任务状态。
- 会话页面还不能选择知识库。

下一小步是 **RAG-6C：知识库编辑、软归档与恢复界面**。它会复用上一模块已经实现的 PATCH、DELETE
和对象级并发状态，补充编辑对话框、归档确认和恢复按钮。

## 16. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 页面直接调用 fetch | 绕过 API Client 和 Store，协议与状态混在组件中 |
| 页面刚打开先闪“没有知识库” | 只检查数组长度，没有区分 hasLoaded |
| 创建错误在两个区域重复 | 没区分表单错误与列表错误 |
| 创建成功后仍保留旧输入 | 没在后端确认成功后清理本地表单 |
| 纯空格描述进入数据库 | 没有 trim 并转换为 null |
| 未登录只能跳转但旧数据仍在 | 路由守卫没有清理私有 Store |
| 手机页面挤成两列 | 没有给知识库布局增加响应式断点 |
| 页面允许修改向量维度 | 把只读配置快照误当作编辑字段 |

## 17. 面试表达

> 我将知识库页面拆成组件本地表单状态和 Pinia 服务器状态。页面只调用 Store，不处理 HTTP；创建前
> 做即时校验，后端仍负责最终约束。列表通过 hasLoaded 区分首次加载和真实空数据，失败时保留旧列表。
> 我还把会话、消息和知识库的退出清理收口到统一函数，路由守卫拒绝访问时也会清理旧用户状态，避免
> 只隐藏页面却残留私有数据。桌面和移动端复用同一组件，通过 CSS 网格切换布局。

## 18. 小练习（约 15–20 分钟）

给 `KnowledgeBasesView.test.ts` 增加“纯空格描述转换为 null”的测试：

1. 列表接口返回空数组。
2. 创建接口返回一条知识库。
3. 名称填写“测试资料库”。
4. 描述填写三个空格。
5. 提交表单。
6. 断言 API 收到 `{ name: "测试资料库", description: null }`。

运行：

```powershell
npm run test:run -- KnowledgeBasesView.test.ts
```

答案要点：组件在发送请求前使用 `trim()`，空结果通过 `normalizedDescription || null` 转成数据库空值。
