# 会话知识库绑定与消息引用快照

## 1. 本模块解决什么问题

上一模块已经能对单个知识库执行向量检索，但聊天系统还缺少两个持久状态：

1. 某个会话允许搜索哪些知识库。
2. 某条助手消息当时引用了哪些证据。

因此新增两张表：

```text
conversations
  ↓ 一对多
conversation_knowledge_bases
  ↓ 多对一
knowledge_bases

messages
  ↓ 一对多
message_citations
  ├─ 当前 knowledge_base/document/chunk 外键
  └─ 文件名、页码、原文、顺序和分数快照
```

本模块只建立数据结构。下一模块才实现会话绑定 API、所有权校验和 Repository。

## 2. 为什么会话与知识库需要单独的关联表

一个会话可能同时使用项目规范库、会议资料库和产品文档库；一个知识库也可能被多个会话
选择，所以它们是多对多关系。

不能在 `conversations` 中保存逗号分隔的知识库 ID，也不应该把 ID 数组塞进 JSON：

- 数据库无法为每个 ID 建立可靠外键。
- 无法高效查询某个知识库被哪些会话使用。
- 删除知识库时难以自动清理绑定。
- 重复 ID、无效 ID 和并发更新都更难约束。

显式关联表能够使用外键、主键和索引表达这些规则。

## 3. 为什么关联表没有 UUID

`conversation_knowledge_bases` 的业务身份就是：

```text
(conversation_id, knowledge_base_id)
```

两列共同作为复合主键，表示同一会话只能绑定同一知识库一次。即使两个请求并发插入，
数据库也会拒绝重复组合。

如果再增加独立 UUID，仍然需要给这两列增加唯一约束，否则会出现：

```text
id=A，会话1，知识库1
id=B，会话1，知识库1
```

UUID 并不是所有表的固定答案。独立业务实体适合 UUID；纯关联关系通常更适合自然复合主键。

## 4. 为什么引用不能只保存 chunk_id

文档重新入库时，Worker 会原子删除旧 Chunk，再写入新 Chunk。即使新旧文本相似，它们的
Chunk ID、分块边界和排序都可能改变。

如果引用表只保存 `chunk_id`：

```text
回答生成并引用旧 Chunk
→ 文档重新入库
→ 旧 Chunk 被删除
→ 历史消息刷新后无法展示来源
```

因此 `message_citations` 同时保存两类数据。

### 活引用

- `knowledge_base_id`
- `document_id`
- `chunk_id`

它们用于来源仍存在时进行跳转、审计和关联查询。

### 不可变快照

- `knowledge_base_name_snapshot`
- `filename_snapshot`
- `chunk_index_snapshot`
- `content_snapshot`
- `heading_path_snapshot`
- `page_start`、`page_end`
- `source_order`
- `retrieval_score`

快照记录的是“回答生成当时模型实际看到的证据”，不能在文档后来更新时跟着修改。

## 5. CASCADE 和 SET NULL 分别是什么意思

| 被删除的记录 | 外键动作 | 原因 |
| --- | --- | --- |
| 会话 | 删除知识库绑定 | 会话不存在后，绑定没有独立意义 |
| 知识库 | 删除会话绑定 | 不能继续让会话绑定不存在的知识库 |
| 消息 | 删除引用 | 回答本身不存在后，引用没有展示和审计主体 |
| 知识库来源 | 引用中的知识库 ID 置空 | 保留历史引用快照 |
| 文档来源 | 引用中的文档 ID 置空 | 保留文件名、页码和原文快照 |
| Chunk 来源 | 引用中的 Chunk ID 置空 | 重新入库后仍能展示旧证据 |

`CASCADE` 表示父记录删除时子记录一起删除；`SET NULL` 表示子记录保留，只清空已经失效的
外键。使用 `SET NULL` 的字段必须允许 `NULL`，否则数据库无法执行删除动作。

## 6. 引用表有哪些数据库保护

### 来源顺序

```text
UNIQUE(message_id, source_order)
CHECK(source_order > 0)
```

同一条回答不能同时出现两个 `[S1]`，来源编号从 1 开始。

### 检索分数

```text
-1 <= retrieval_score <= 1
```

余弦相似度的合法范围由数据库再次保护，防止异常值进入历史消息。

### Chunk 和页码

- Chunk 序号不能小于 0。
- 页码存在时必须大于 0。
- 结束页不能早于开始页。

### 必要快照

知识库名称、文件名和原文快照不能为空字符串。外键可以失效，但审计所需的展示事实不能
一起丢失。

## 7. 为什么外键不能直接保证跨用户隔离

数据库外键只能保证：

```text
conversation_id 确实存在
knowledge_base_id 确实存在
```

它不能自动表达：

```text
conversation.user_id == knowledge_base.user_id
```

这是跨两张父表的业务规则，普通外键无法完成。下一模块的 Service 会在同一个事务中：

1. 按当前用户查询会话。
2. 按当前用户查询所有知识库。
3. 只有全部属于当前用户且未归档，才写入绑定。

所有权过滤仍必须出现在查询 SQL 中，不能相信客户端提交的 ID。

## 8. 索引为什么不仅用于查询

关联表给 `knowledge_base_id` 建立反向索引，可以快速查询一个知识库的全部绑定。

引用表给知识库、文档和 Chunk 外键建立索引，不只是为了以后做审计查询。删除父记录执行
`SET NULL` 时，PostgreSQL 也需要定位所有引用它的行；没有索引时可能扫描整张引用表。

`UNIQUE(message_id, source_order)` 自身会创建唯一索引，因此按消息读取并排序引用不需要再
建立完全重复的普通索引。

## 9. 迁移怎样执行

正式数据库升级：

```powershell
Set-Location backend
uv run alembic current
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

预期新 head：

```text
e8f4a1c7d205
```

`alembic check` 返回 `No new upgrade operations detected.`，表示 SQLAlchemy 实体与数据库
结构一致，没有忘记生成迁移。

### 为什么临时空库要使用 postgresql+psycopg

SQLAlchemy URL 不只描述数据库地址，还指定 Python 驱动：

```text
postgresql+psycopg://postgres:postgres@localhost:5432/database_name
```

如果写成普通 `postgresql://`，同步 Alembic 进程可能按默认规则寻找 psycopg2。本项目安装的
是 psycopg 3，因此临时覆盖数据库地址时要显式写 `+psycopg`。应用异步 Session 则会使用
`postgresql+psycopg` 的异步连接模式。

## 10. 本次真实数据库怎样验收

升级前后业务数据数量保持不变：

```text
用户 5
会话 3
消息 10
知识库 1
文档 2
Chunk 7
```

随后在一个事务中创建临时会话、绑定、消息和引用：

```text
删除 Chunk
→ citation.chunk_id = NULL
→ content_snapshot 仍存在

删除文档
→ citation.document_id = NULL
→ filename/content 快照仍存在

ROLLBACK
→ 临时绑定与引用归零
→ 正式文档和 Chunk 恢复原数量
```

另外使用临时空数据库完成了从 base 到 head 的完整升级，并把本次迁移降回旧 head，确认
两张新表按依赖顺序移除。临时数据库最终已经删除。

## 11. 关键代码入口

- 会话知识库绑定实体：
  `backend/app/modules/conversation/entity/conversation_knowledge_base.py`
- 消息引用实体：
  `backend/app/modules/conversation/entity/message_citation.py`
- 会话模型聚合入口：
  `backend/app/modules/conversation/models.py`
- Alembic 元数据入口：
  `backend/migrations/env.py`
- 新迁移：
  `backend/migrations/versions/e8f4a1c7d205_create_conversation_kb_bindings_and_citations.py`
- 结构契约测试：
  `backend/tests/test_rag_conversation_citation_foundation.py`

## 12. 本模块尚未做什么

- 没有会话绑定知识库的 GET/PUT API。
- 没有绑定 Repository 和 Service。
- 没有把多个知识库交给检索 Service。
- 没有在助手消息事务中写入引用。
- 没有在 SSE 中发送 `sources`。

这些会在后续小步逐层连接，避免一次引入多个难以理解的事务和流式状态。

## 13. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| Alembic 没发现新表 | 检查 `conversation.models` 是否被 `migrations/env.py` 导入 |
| 删除 Chunk 时引用也消失 | 外键错误使用了 CASCADE，应使用 SET NULL |
| SET NULL 删除失败 | 对应外键列没有设置 nullable |
| 同一消息出现两个 S1 | 缺少 `(message_id, source_order)` 唯一约束 |
| 空库迁移寻找 psycopg2 | 临时 URL 显式使用 `postgresql+psycopg://` |
| 绑定了其他用户知识库 | 外键不负责所有权，必须由 Service 按用户过滤 |

## 14. 面试表达

> 我把会话和知识库建模为显式多对多关联，使用两个外键组成复合主键，直接保证重复绑定
> 不会发生。消息引用同时保存可空的知识库、文档和 Chunk 活引用，以及不可变的文件名、
> 页码和原文快照。来源重新入库或删除时外键使用 SET NULL，历史回答仍可审计；消息删除时
> 引用使用 CASCADE。数据库外键不能表达会话与知识库属于同一用户，因此所有权会在下一层
> Service 的事务中校验。

## 15. 小练习（约 15 分钟）

打开：

```text
backend/tests/test_rag_conversation_citation_foundation.py
```

在 `test_message_citation_has_order_score_page_and_reverse_lookup_guards` 中补充三个断言，确认以下
约束存在：

```text
ck_message_citations_knowledge_base_name_nonempty
ck_message_citations_filename_nonempty
ck_message_citations_content_nonempty
```

完成步骤：

1. 先观察测试里已有的 `constraint_names` 集合。
2. 增加三个 `assert`。
3. 运行：
   `uv run pytest tests/test_rag_conversation_citation_foundation.py -q`
4. 思考为什么外键可以为 NULL，但三个快照不能是空字符串。

答案要点：活来源可能因删除或重新入库失效；快照是历史回答最后的审计证据，所以必须保留。
