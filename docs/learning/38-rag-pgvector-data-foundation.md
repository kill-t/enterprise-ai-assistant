# RAG 数据地基：pgvector 与四张核心表

## 1. 本模块解决什么问题

RAG 不能只有“把文档切开再搜索”的函数。系统还需要回答下面的问题：

- 这份文档属于哪个用户和知识库？
- 原文件保存在哪里，是否重复上传？
- 文档正在处理、已经可检索，还是处理失败？
- Worker 重启后，未完成的任务怎样找回来？
- 回答引用的 Chunk 来自哪份文件、哪一页？

本模块先建立数据结构，不处理文件上传，也不调用 Embedding 模型。这样后续模块可以
围绕稳定的数据契约逐步实现，而不是把上传、解析、向量化和检索塞进一个函数。

## 2. 四张表的关系

```text
users
  └─ knowledge_bases
       └─ documents
            ├─ document_ingestion_jobs
            └─ document_chunks
```

### `knowledge_bases`

知识库属于一个用户。`embedding_provider`、`embedding_model` 和
`embedding_dimension` 是创建知识库时的配置快照。

向量不能只看“长度一样”就混用。两个不同模型即使都输出 1024 维，它们的坐标含义也
不同，因此后续检索必须确认查询向量与入库向量来自同一模型。

### `documents`

文档保存用户看到的文件名、MIME 类型、文件大小、SHA-256、MinIO Object Key 和
入库状态。唯一约束 `(knowledge_base_id, sha256)` 用于阻止同一知识库重复上传相同
内容；文件名相同但内容不同仍可以上传。

`documents.status` 表示这份文档当前是否可被业务使用：

```text
PENDING → PROCESSING → READY
                    └→ FAILED
READY / FAILED → DELETED
```

本模块只定义状态，具体状态转换由后续入库 Service 和 Worker 实现。

### `document_ingestion_jobs`

文档状态描述“文档现在怎么样”，任务状态描述“本次处理执行到哪里”。两者不能合成
一个字段。例如一次任务失败后，用户可以重试并产生新任务，文档仍是同一份文档。

任务表保存幂等键、重试次数、Celery Task ID、下一次重试时间和投递租约。同一文档
只能同时存在一个 `PENDING` 或 `RUNNING` 任务，避免两个 Worker 重复入库。

### `document_chunks`

Chunk 保存正文、顺序、标题路径、页码、Token 数和内容哈希。`embedding` 使用
`vector(1024)`，以后可以直接按余弦距离排序。

页码和标题不是装饰字段。回答展示“来源：项目手册第 7 页”时，必须从 Chunk 的持久化
元数据生成，不能让大模型自己猜页码。

## 3. pgvector 在这里负责什么

pgvector 是 PostgreSQL 扩展。迁移先执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

随后表才能声明：

```sql
embedding vector(1024)
```

HNSW 是近似最近邻索引，当前索引使用 `vector_cosine_ops`，对应余弦距离。它负责减少
候选搜索范围，但不会替代用户所有权和文档状态过滤。后续检索仍必须在 SQL 条件中限制
知识库和 `READY` 文档。

Python 侧还需要在每条 psycopg 异步连接建立时注册 vector 编解码器。这一步只负责把
Python 的 `list[float]` 与 PostgreSQL `vector` 类型互相转换；创建数据库扩展仍属于
Alembic 迁移的职责。

## 4. 实体和迁移为什么都要写

SQLAlchemy Entity 是应用运行时看到的数据模型；Alembic Migration 是数据库从旧版本
升级到新版本的可执行历史。

只写 Entity 不会自动修改现有数据库，只写 Migration 又会让应用代码不知道如何读写
新表。因此本模块同时维护两者，并用下面命令检查是否一致：

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m alembic check
```

输出 `No new upgrade operations detected.` 表示实体元数据与当前数据库一致。

## 5. 本地升级命令与数据保护

当前数据卷最初由基于 Debian trixie 的 `postgres:17` 创建，因此 pgvector 镜像也使用
`pg17-trixie`。如果换成 bookworm，会产生排序规则版本不匹配警告。

升级顺序：

```powershell
# 仓库根目录；停止但不移除 Docker Desktop 中的容器
docker compose -f infra/docker/docker-compose.yml stop postgres

# 按 Compose 中的新镜像重建同名容器，继续挂载原 postgres_data
docker compose -f infra/docker/docker-compose.yml up -d postgres

Set-Location backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

修改数据库镜像或执行迁移前应使用 `pg_dump -Fc` 创建备份。不要执行
`docker compose down -v`，因为 `-v` 会删除命名数据卷。

## 6. 本模块测试保护什么

- 知识库必须拥有用户外键和 1024 维配置约束。
- 文档必须拥有来源定位、去重约束和状态字段。
- 同一文档不能同时存在两个活动入库任务。
- Chunk 必须包含追溯字段、1024 维向量和 HNSW 余弦索引。
- RAG 迁移必须接在原迁移 head 后，并且降级时不擅自删除共享的 vector 扩展。
- Compose 必须复用原容器名和原命名数据卷。
- 从空数据库可以沿全部迁移升级到最新版本。

“Entity 能导入”只是单元层验证；真实执行迁移并查询扩展、表和索引属于数据库集成验证。

## 7. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| `type "vector" does not exist` | PostgreSQL 镜像没有 pgvector，或迁移未先创建扩展。 |
| `No module named psycopg2` | 连接串写成了未指定驱动的 `postgresql://`；本项目使用 `postgresql+psycopg://`。 |
| `collation version mismatch` | 新旧镜像底层发行版不同；保持同一 PostgreSQL 大版本和 Debian 代号。 |
| Alembic 没发现新表 | `migrations/env.py` 没有导入新模块实体。 |
| Docker Desktop 中容器消失 | 使用了 `compose down`；本地日常停止使用 `compose stop`。 |

## 8. 面试表达

> 我把 RAG 设计成知识库领域模块，而不是一个直接调用向量库的 Tool。PostgreSQL 保存
> 用户所有权、文档状态、持久化任务和 Chunk，pgvector 负责向量距离与 HNSW 索引。
> 原文件后续进入 MinIO，Redis 不承担 RAG 事实。入库任务与文档状态分离，使失败重试
> 和进程恢复不会破坏文档身份；页码等来源元数据随 Chunk 持久化，为后续引用审计提供
> 事实依据。

## 9. 小练习

请用自己的话回答：为什么 `documents.status` 和
`document_ingestion_jobs.status` 不能只保留一个？

答案要点：文档是长期业务资源，一份文档可以经历多次入库尝试；任务是某一次执行记录，
拥有独立的尝试次数、错误和时间。把两者合并后，会丢失重试历史，也难以判断文档是否已有
另一个任务正在处理。
