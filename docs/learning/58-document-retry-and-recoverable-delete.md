# 文档手动重试与可恢复删除

## 1. 为什么先做这个模块

准备实现 Vue 文档状态层时，检查发现后端只有：

```text
POST /knowledge-bases/{id}/documents
GET  /knowledge-bases/{id}/documents
GET  /documents/{id}
```

既定计划中的手动重试和删除接口尚未存在。如果直接写前端，会得到调用不存在 URL 的假功能。因此先
补齐后端文档生命周期：

```text
POST   /api/v1/documents/{id}/retry
DELETE /api/v1/documents/{id}
```

## 2. 文档与任务为什么是两个状态

文档状态描述业务结果：

```text
PENDING → PROCESSING → READY
                     ↘ FAILED
READY / FAILED → DELETED
```

任务状态描述某一次后台执行：

```text
PENDING → RUNNING → SUCCEEDED
                  ↘ FAILED
```

一个文档可以先有一条失败任务，用户手动重试后再有一条新任务。文档是当前业务状态，任务是每次执行
历史，不能把两者合并成同一张表或同一个状态字段。

## 3. 为什么手动重试创建新任务

错误做法是把旧任务的 `attempts` 和状态重置为初始值。这样会丢失：

- 上一次执行了几次。
- 最终错误是什么。
- 何时失败。
- 用户是否主动发起过重试。

当前流程是：

```text
读取当前用户文档
→ 确认知识库未归档
→ 确认文档是 FAILED
→ FAILED 条件更新为 PENDING
→ 创建全新的 PENDING job
→ 单个数据库事务提交
→ 提交成功后投递 Celery
```

旧任务仍然保留，新任务使用独立 UUID 和幂等键：

```text
document-ingestion:{document_id}:manual:{job_id}
```

## 4. 为什么只有 FAILED 可以重试

不同状态的含义是：

| 文档状态 | 是否允许手动重试 | 原因 |
| --- | --- | --- |
| PENDING | 否 | 已经有待执行任务 |
| PROCESSING | 否 | Worker 正在处理 |
| READY | 否 | 已经成功，无需重试 |
| FAILED | 是 | 没有活动任务，需要新执行历史 |
| DELETED | 否 | 已进入删除恢复流程 |

非法状态返回：

```text
409 DOCUMENT_NOT_RETRYABLE
```

归档知识库中的失败文档也不能重试，用户必须先恢复知识库。

## 5. 并发重试怎样只成功一次

仅在 Service 中先检查 `document.status == FAILED` 不够。两个 API 实例可能同时读到 FAILED：

```text
请求 A 读取 FAILED
请求 B 读取 FAILED
请求 A 创建任务
请求 B 也创建任务
```

Repository 使用带旧状态的条件更新：

```sql
UPDATE documents
SET status = 'PENDING', ...
WHERE id = :id AND status = 'FAILED'
RETURNING ...
```

数据库行锁和条件重检保证只有一个请求拿到返回行。另一个请求得到 `None`，回滚并返回 409。数据库还
有“每个文档最多一条 PENDING/RUNNING 任务”的部分唯一索引，形成第二层保护。

## 6. 为什么先提交数据库再投递 Celery

重试同样遵守持久化任务原则：

```text
提交 document=PENDING + new job=PENDING
→ 再向 Redis Broker 发布 Celery 消息
```

如果先发布，Worker 可能在数据库事务提交前启动，读取不到任务。当前投递失败不会回滚已提交任务，
恢复扫描器以后仍能重新投递 PENDING 记录。

## 7. 删除为什么不能只做逻辑删除

`documents` 有唯一约束：

```text
(knowledge_base_id, sha256)
```

如果 DELETE 永远只把状态改成 DELETED，记录仍占用唯一约束，同一文件以后无法重新上传。因此删除
最终必须释放数据库记录和 MinIO 对象。

但是 PostgreSQL 事务不能回滚 MinIO 操作，不能假装两者具有原子事务。当前使用 DELETED 作为跨系统
恢复墓碑。

## 8. 两阶段删除流程

```text
1. 校验用户所有权
2. 只允许 READY / FAILED 进入删除
3. 条件更新为 DELETED 并提交
4. 删除 MinIO 原文件
5. DELETE 数据库文档墓碑并提交
6. 外键级联删除 jobs 和 chunks
7. message_citations.document_id 变 null，引用快照保留
```

为什么先提交 DELETED：

- 文档立即停止被列表和检索读取。
- 记录跨系统清理进行到哪一步。
- 服务重启后仍有持久化恢复点。

为什么最后硬删除：

- 释放 SHA-256 唯一约束。
- 清理任务和 Chunk。
- 让相同内容以后可以重新上传。

## 9. 删除失败怎样恢复

### MinIO 删除失败

```text
DELETED 已提交
→ MinIO 返回错误
→ 返回 503 OBJECT_STORAGE_UNAVAILABLE
→ 保留墓碑
→ 不硬删数据库记录
```

重复调用同一个 DELETE 时，Service 读取包含 DELETED 的所有权查询，跳过第一阶段并继续清理对象。

### MinIO 成功、数据库硬删除失败

```text
原对象已不存在
→ 墓碑硬删除提交失败
→ 回滚第二个事务
→ DELETED 墓碑仍在
```

重复 DELETE 会再次执行幂等对象删除，再尝试清理墓碑。

当前前端下一模块会在失败时保留文档 ID，允许用户重试。以后还需要增加周期墓碑清理扫描器，处理客户端
断开后没有再次调用 DELETE 的情况。

## 10. 为什么入库期间拒绝删除

任务表目前没有 CANCELLED 状态，也没有 Worker 取消协议。如果允许删除 PENDING 或 PROCESSING 文档：

```text
API 标记 DELETED
→ Worker 继续执行
→ 迟到事务把文档写回 READY
```

因此第一版返回：

```text
409 DOCUMENT_BUSY
```

用户等待 READY 或 FAILED 后再删除。以后实现任务取消时，必须同时设计 Celery 撤销、Worker 协作取消和
数据库条件更新，不能只加一个按钮。

## 11. 删除后历史引用为什么仍可审计

`message_citations` 已保存：

- 文件名快照。
- 页码快照。
- Chunk 内容快照。
- 检索分数和来源顺序。

数据库文档最终硬删除时，引用外键使用 `ON DELETE SET NULL`。旧回答仍显示当时证据，并标记为历史
快照，不会因为当前文档删除就篡改过去回答。

## 12. 权限边界

重试和删除都先通过知识库关系过滤：

```text
documents.id = document_id
AND knowledge_bases.user_id = current_user.id
```

不存在和跨用户访问统一返回 404，避免泄露别人的文档 ID 是否存在。删除恢复查询虽然允许读取 DELETED
墓碑，但仍必须包含相同所有权过滤。

## 13. HTTP 契约

### 手动重试

```http
POST /api/v1/documents/{document_id}/retry
202 Accepted
```

响应：

```text
DocumentRetryResponse
├─ document（已恢复为 PENDING）
├─ job_id（新的任务 ID）
└─ job_status（PENDING）
```

### 删除

```http
DELETE /api/v1/documents/{document_id}
204 No Content
```

只有对象和数据库墓碑都清理完成后才返回 204。中途失败返回明确错误，客户端保留 ID 后可以重试。

## 14. 关键代码入口

- HTTP 路由：`backend/app/modules/knowledge/api/document_router.py`
- 请求响应契约：`backend/app/modules/knowledge/schema/document_schema.py`
- 文档业务编排：`backend/app/modules/knowledge/service/document_service.py`
- 原子状态转换：`backend/app/modules/knowledge/repository/document_repository.py`
- 稳定错误码：`backend/app/common/exception/error_code.py`
- Service、SQL 和 HTTP 测试：`backend/tests/test_document_upload.py`

## 15. 测试保护了什么

新增测试覆盖：

- FAILED 文档创建新任务并在提交后投递。
- 非 FAILED 文档返回 409。
- 归档知识库拒绝重试。
- 并发重试失败者回滚且不投递。
- Repository 更新包含 FAILED 条件。
- 新任务保留独立幂等键。
- READY/FAILED 两阶段删除成功路径。
- PENDING/PROCESSING 拒绝删除。
- 逻辑删除 SQL 只允许终态。
- MinIO 失败保留已提交墓碑。
- 重复 DELETE 从墓碑继续清理。
- 硬删除 SQL 只允许 DELETED。
- HTTP 重试返回 202。
- HTTP 删除返回 204 空正文。
- OpenAPI 中重试和删除均要求 Bearer Token。

最终验证：

```text
后端：338 passed
Ruff：通过
前端回归：107 passed
TypeScript 与 Vite build：通过
```

本模块没有修改表结构，不需要 Alembic 迁移；测试不需要启动 Docker 或模型服务。

## 16. 本模块尚未做什么

- 没有 DELETED 墓碑周期清理扫描器。
- 没有活动入库任务取消协议。
- 没有 Vue 文档 API Client 和 Store。
- 没有上传页面、状态轮询和重试按钮。
- 没有文档详情或下载接口。

下一小步是 **RAG-6E：Vue 文档 API 与状态层**。它会严格对接现在真实存在的上传、列表、详情、重试和
删除接口，并按知识库 ID 隔离前端文档状态。

## 17. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 手动重试重置旧 job | 丢失执行历史，应创建新任务 |
| 两个并发请求都重试成功 | 只在 Python 预检查，没有条件 UPDATE |
| 事务没提交就投递 Celery | Worker 可能读不到任务 |
| DELETED 永久留在数据库 | 唯一 SHA 约束导致相同文件无法重传 |
| 先删 MinIO 再更新数据库 | 数据库失败后会留下指向不存在对象的活动文档 |
| MinIO 失败却硬删墓碑 | 丢失跨系统清理恢复点 |
| PROCESSING 文档允许删除 | 迟到 Worker 可能覆盖删除状态 |
| 删除文档后旧回答无引用 | 没保存 citation 快照或外键不是 SET NULL |
| 跨用户 DELETE 返回 403 | 泄露资源存在性，应统一返回 404 |

## 18. 面试表达

> 文档手动重试不会重置旧任务，而是在 FAILED 条件更新和新任务插入的同一事务中完成，提交后再投递
> Celery。并发正确性由数据库条件更新和活动任务部分唯一索引保证。文档删除涉及 PostgreSQL 和 MinIO，
> 我没有伪造分布式事务，而是先提交 DELETED 墓碑、再删对象、最后硬删数据库释放 SHA 唯一约束。
> 任一步失败都可用同一 ID 继续清理，历史 citation 外键变 null 但文件名、页码和原文快照仍可审计。

## 19. 小练习（约 20 分钟）

为“数据库墓碑硬删除失败”补一个 Service 测试：

1. 构造状态为 DELETED 的文档。
2. MinIO `delete_object` 成功。
3. `hard_delete_if_marked` 抛出数据库异常。
4. 调用 `delete_document`。
5. 断言 MinIO 删除已执行。
6. 断言数据库执行 rollback。
7. 断言异常继续抛出，没有错误返回 204。

答案要点：对象删除已经发生，无法回滚；数据库墓碑必须保留，使下一次 DELETE 可以幂等继续。
