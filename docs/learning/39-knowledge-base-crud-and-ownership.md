# 知识库 CRUD、所有权与事务边界

## 1. 这一小步到底在做什么

上一模块只是建立了 `knowledge_bases` 数据表。数据库里“可以存知识库”，但用户还没有
安全的 HTTP 接口去操作它。

本模块补上下面五个接口：

```text
POST   /api/v1/knowledge-bases                  创建
GET    /api/v1/knowledge-bases                  列表
GET    /api/v1/knowledge-bases/{id}             详情
PATCH  /api/v1/knowledge-bases/{id}             修改或恢复归档
DELETE /api/v1/knowledge-bases/{id}             软归档
```

它还没有上传文件、分块和向量检索。可以把当前的知识库理解为一个刚创建好的“空文件夹”，
下一小步才允许把 PDF、TXT 或 Markdown 放进去。

## 2. 一次请求怎样流动

以创建知识库为例：

```text
Vue / API 调试工具
  ↓ JSON：name、description
FastAPI Router
  ↓ 从 JWT 依赖取得 current_user.id
KnowledgeBaseService
  ↓ 检查同名、决定提交或回滚
KnowledgeBaseRepository
  ↓ 只组织 SQL 和操作 ORM
PostgreSQL knowledge_bases
  ↓
KnowledgeBaseResponse → JSON
```

最重要的一点是：客户端不能提交 `user_id`。所有者必须来自已经校验过的 JWT。否则恶意
用户可以在请求体里填写另一个人的 ID，把资源创建到别人名下。

## 3. 每一层分别负责什么

### Schema

入口：`app/modules/knowledge/schema/knowledge_base_schema.py`

- `KnowledgeBaseCreate` 校验创建请求。
- `KnowledgeBaseUpdate` 校验 PATCH 请求。
- `KnowledgeBaseResponse` 控制哪些字段可以返回给前端。

名称会先去掉两端空格，所以 `"  项目资料  "` 最终保存为 `"项目资料"`。纯空格名称会在
长度校验时失败。纯空格描述转换为 `None`，避免数据库出现没有意义的空白字符串。

PATCH 要区分两种情况：

```text
{}
```

表示什么都没改，应返回校验错误；而：

```json
{"description": null}
```

表示用户明确要求清空描述，是合法操作。`model_fields_set` 用于判断一个字段究竟没传，
还是传了 `null`。

### Repository

入口：`app/modules/knowledge/repository/knowledge_base_repository.py`

Repository 只负责数据库访问，例如：

```text
WHERE knowledge_bases.id = :id
  AND knowledge_bases.user_id = :current_user_id
```

它可以 `flush`，但不能 `commit`。`flush` 是把当前改动发送给数据库，让约束得到检查；
事务仍然可以回滚。`commit` 表示整个业务动作已经确认完成，只能由了解完整业务流程的
Service 决定。

Repository 通过 `knowledge.models` 聚合入口加载知识模块全部实体。原因是 SQLAlchemy 的
`relationship("DocumentEntity")` 虽然使用字符串，但创建实体前仍要确保目标模型已经被
Python 导入和注册。

### Service

入口：`app/modules/knowledge/service/knowledge_base_service.py`

Service 负责真正的业务规则：

- 用当前用户 ID 查询资源。
- 同一用户不能创建完全同名的知识库。
- 创建时冻结 Embedding Provider、模型和维度。
- 修改成功后提交，失败后回滚。
- 删除请求执行软归档。
- 将数据库错误转换为稳定的业务错误。

这里的事务语义是：

```text
Repository flush 成功
→ Service commit
→ 对外返回成功
```

任一数据库写操作或提交失败：

```text
捕获异常
→ rollback
→ 异常继续交给全局错误处理器
```

### Dependency

入口：`app/modules/knowledge/dependencies.py`

它把 FastAPI 当前请求的 `AsyncSession` 交给 `KnowledgeBaseService`。同一请求里的 Service
和 Repository 因此使用同一个 Session，也就是同一条事务边界。

### Router

入口：`app/modules/knowledge/api/knowledge_base_router.py`

Router 只处理 HTTP 概念：路径、状态码、查询参数和当前用户依赖。它不自己拼 SQL，也不
决定名称是否重复。

## 4. 为什么跨用户访问也返回 404

假设用户甲猜到了用户乙的知识库 UUID。如果系统返回：

```text
403：你无权访问这个知识库
```

这等于告诉甲：“这个 UUID 真实存在，只是不属于你。”攻击者可以用响应差异枚举资源。

本项目直接使用带所有权条件的查询：

```text
id 正确 + user_id 正确 → 返回实体
其他所有情况          → KNOWLEDGE_BASE_NOT_FOUND / 404
```

所以“不存在”和“属于别人”在外部看起来完全一样。这叫避免资源存在性泄露。

## 5. 为什么同名检查要做两次

Service 先查询一次同名知识库，是为了给用户友好的 `409` 提示。但两个并发请求可能这样
执行：

```text
请求 A：查询，不存在
请求 B：查询，不存在
请求 A：插入成功
请求 B：也尝试插入
```

因此仅靠“先查询再插入”不可靠。数据库中还有
`UNIQUE(user_id, name)`，它负责并发情况下的最终保护。第二个插入触发唯一约束后，
Service 回滚事务并转换为：

```text
HTTP 409
KNOWLEDGE_BASE_NAME_CONFLICT
知识库名称已存在
```

可以这样记：Service 预检查改善体验，数据库约束保证事实正确。

## 6. 为什么 DELETE 实际执行软归档

知识库以后会关联文档、MinIO 原文件、Chunk、向量和引用审计。如果当前直接物理删除，
很容易出现以下问题：

- 用户误删后无法恢复。
- 数据库已删除，但 MinIO 删除失败，留下孤立文件。
- 旧回答的来源记录难以审计。

所以当前 DELETE 只把 `is_archived` 改成 `true`：

- 普通列表默认不显示归档项。
- `?include_archived=true` 可以查看全部。
- PATCH `{"is_archived": false}` 可以恢复。
- 重复 DELETE 是幂等操作，不重复更新数据库。

以后若增加“永久删除”，要单独设计二次确认、对象存储补偿和后台清理任务。

## 7. Embedding 配置快照现在有什么用

知识库创建时保存：

```text
provider  = mock
model     = mock-hash-1024
dimension = 1024
```

当前还没有真正生成向量，但先冻结配置可以防止未来发生这种错误：旧文档用模型 A 入库，
查询却切换成模型 B。即使 A 和 B 都输出 1024 维，两套向量空间也不能直接比较。

这些默认值目前集中在 `app/modules/knowledge/constants.py`。等 Embedding Gateway 实现后，
它们会由受校验的配置提供，而不是允许前端任意填写。

## 8. 测试分别保护什么

本模块测试不是独立于项目的另一套程序。测试会直接导入项目中的 Schema、Repository、
Service 和 FastAPI 应用，然后替换数据库或依赖以观察某个行为。

### 单元测试

- Schema 是否清理空格、拒绝空 PATCH。
- Repository SQL 是否同时包含资源 ID 和用户 ID。
- Service 是否提交、回滚、阻止同名和执行软归档。
- 不存在或跨用户资源是否统一得到 404。

### HTTP 协议测试

使用 HTTPX 向内存中的真实 FastAPI 路由发送请求，验证：

- POST 返回 `201`。
- JSON 请求能转换成 Schema。
- JWT 依赖恢复的用户 ID 会传给 Service。
- 响应包含 Embedding 配置快照。

### 数据库验证

本次还连接现有 PostgreSQL，在外层测试事务中执行了：

```text
创建 → 修改 → 列表 → 归档 → 整体回滚
```

回滚后再次按临时 UUID 查询，记录数是 0，因此没有污染本地数据。

## 9. 本次遇到的典型错误

### SQLAlchemy 找不到 `DocumentEntity`

报错关键词：

```text
relationship 'DocumentEntity' failed to locate a name
```

原因不是数据库没有表，而是 Python 进程还没导入该 ORM 类。修复方式是让 Repository 通过
知识模块的模型聚合入口加载完整实体关系图。

### Windows 下 psycopg 拒绝 ProactorEventLoop

报错关键词：

```text
Psycopg cannot use the 'ProactorEventLoop'
```

Windows 手工运行异步数据库脚本时也要使用 Selector 事件循环。正式本地服务已经通过
`python -m app.local_server` 处理这个兼容问题。

### 把 `commit` 写进 Repository

这样做会让 Service 无法把多个数据库动作组成一个完整事务。Repository 只 `flush`，
Service 在业务动作全部完成后再 `commit`。

## 10. 面试时可以怎样表达

> 我把知识库接口按 Router、Service、Repository 分层。所有权不是查询后再单独判断，
> 而是直接进入 SQL 条件，跨用户访问与资源不存在统一返回 404。Service 管理事务和名称
> 冲突，数据库唯一约束处理并发竞争。DELETE 第一版采用幂等软归档，为后续 MinIO 文件、
> Chunk 和引用审计保留恢复及补偿空间。创建知识库时冻结 Embedding 配置，防止以后混用
> 不同向量空间。

## 11. 小练习

请用 15–30 分钟完成：给知识库名称增加一条规则——名称中不能包含换行符 `\n` 或
`\r`。

建议只修改两处：

1. 在 `KnowledgeBaseCreate` 和 `KnowledgeBaseUpdate` 共用的名称清理函数中增加判断。
2. 在 `test_knowledge_base_crud.py` 增加创建和修改两个失败用例。

你完成后告诉我，我会检查：

- 是在 Schema 层处理输入规则，还是错误地放到了 Router。
- 创建和修改是否复用了同一规则。
- 测试是否先证明包含换行符的名称会失败。

答案要点：这是输入格式规则，应该在 Schema 入口统一拒绝；Service 不需要知道字符串中
是否有换行，Repository 更不应该负责 Pydantic 校验。
