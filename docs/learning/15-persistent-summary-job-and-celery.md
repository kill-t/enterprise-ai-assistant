# 持久化摘要任务与 Celery 基础

## 1. 本模块做了什么

本模块为自动摘要建立可恢复的后台任务基础，但还没有实现摘要生成逻辑，也没有从聊天流程自动创建任务。

新增内容：

- `conversation_summary_jobs` 持久化任务表。
- `ConversationSummaryJobEntity` ORM 实体。
- 摘要任务 Schema 和 Repository。
- `SummaryJobStateMachine` 状态转换规则。
- Celery 应用和 Redis Broker 配置。
- Celery、本地 Worker 入口、迁移和测试。

## 2. 为什么不能使用 FastAPI 内存后台任务

假设聊天结束后执行：

```text
FastAPI 返回响应
→ 内存后台任务开始生成摘要
→ 服务进程重启
```

如果任务只存在进程内存中，重启后系统不知道：

- 哪个会话需要摘要。
- 摘要准备处理到哪条消息。
- 任务执行过几次。
- 上一次为什么失败。

因此摘要任务必须先成为 PostgreSQL 中的一条事实记录。

## 3. Redis Broker 和 PostgreSQL 的职责

```text
PostgreSQL
→ 保存任务 ID、业务状态、目标游标、重试次数和错误

Redis Broker
→ 告诉 Worker：某个任务 ID 等待执行

Celery Worker
→ 根据任务 ID 查询 PostgreSQL，再执行真正业务逻辑
```

Redis 中的 Celery 消息不是业务事实来源。即使 Celery 不配置 result backend，也可以从 PostgreSQL 查看任务的真实状态。

任务消息以后只需要携带：

```text
job_id
```

不能把全部消息正文、摘要正文或用户敏感数据复制进 Redis Broker。

## 4. 任务状态机

允许的状态转换：

```text
PENDING
→ RUNNING
   → SUCCEEDED
   → FAILED

FAILED
→ PENDING（显式重试）
```

禁止的例子：

```text
PENDING → SUCCEEDED
SUCCEEDED → RUNNING
FAILED → SUCCEEDED
```

为什么需要状态机？

如果每个 Service 或 Worker 随意修改字符串，就可能出现“没有运行就成功”或“成功任务再次运行”等非法状态。

## 5. 为什么要冻结目标消息

任务创建时保存：

```text
target_message_id
target_created_at
```

假设创建任务时会话有 80 条消息：

```text
本次任务目标：总结到消息 60
Worker 执行期间：用户又发送消息 81～90
```

Worker 仍然只处理到消息 60。新消息留给下一次任务，保证本次摘要输入和覆盖游标确定。

如果不冻结目标，Worker 查询过程中不断有新消息进入，摘要边界会变化，测试和重试都难以复现。

## 6. 幂等键是什么

幂等表示：同一个业务动作重复执行，不会产生多个不同结果。

摘要任务以后可以使用类似的键：

```text
summary:{conversation_id}:{target_message_id}
```

数据库对 `idempotency_key` 设置唯一约束。同一个会话、同一个目标消息即使被重复触发，也只能创建一条对应任务。

为什么需要它？

Celery 使用 late acknowledgment 时，Worker 异常可能导致任务再次投递。可靠系统必须按“至少可能执行一次”设计，而不能假设每条消息只执行一次。

## 7. 为什么同一会话只能有一个活动任务

数据库增加了部分唯一索引：

```text
同一个 conversation_id
status 为 PENDING 或 RUNNING
最多一条
```

已经 `SUCCEEDED` 或 `FAILED` 的历史任务可以保留，用于审计和排错。

如果两个 Worker 同时为同一会话更新摘要，可能发生：

```text
Worker A 读取摘要版本 1
Worker B 也读取摘要版本 1
Worker A 写入版本 2
Worker B 覆盖 Worker A 的结果
```

活动任务唯一索引先减少并发来源；后续真正更新摘要时还会增加版本检查和事务保护。

## 8. Celery 配置

当前关键配置：

```text
broker_url = redis://localhost:6379/1
result_backend = None
task_ignore_result = True
task_serializer = json
task_acks_late = True
worker_prefetch_multiplier = 1
soft time limit = 270 秒
hard time limit = 300 秒
```

### 为什么使用 JSON

JSON 比 Python pickle 的安全边界更清晰，也更容易跨语言查看。任务消息以后只传递普通 job ID。

### 为什么不使用 Celery result backend

任务业务状态已经保存在 PostgreSQL。如果同时依赖 Celery result backend，会出现两套状态：

```text
Celery 显示 SUCCESS
PostgreSQL 显示 RUNNING
```

第一版明确规定 PostgreSQL 是唯一事实来源。

### 为什么 late ack 和 prefetch 1

late ack 表示任务执行后再确认消息；`worker_prefetch_multiplier = 1` 避免单个 Worker 提前占用过多长任务。

late ack 可能带来重复执行，因此必须和幂等设计一起使用，不能单独把它理解成“任务绝不会丢”。

## 9. Redis Server 版本和 Python 客户端版本

本模块第一次锁定依赖时发生冲突：

```text
项目 redis-py >= 8.0.1
Celery 5.6 / Kombu Redis transport 要求 redis-py < 6.5
```

这里有两个不同版本：

```text
Docker Redis 8
→ Redis 服务器版本

redis-py 6.4
→ Python 客户端库版本
```

它们不需要版本号相同。项目继续使用 Redis Server 8，同时将 Python 客户端固定到 `>=6.4,<6.5`，满足 Celery 传输层依赖。

遇到依赖冲突时应该阅读解析器给出的依赖链，不应使用强制安装绕过冲突。

## 10. 本次迁移错误

第一次生成离线迁移 SQL 时出现：

```sql
CREATE TYPE summary_job_status ...;
CREATE TYPE summary_job_status ...;
```

原因是迁移先手动创建 Enum，`create_table()` 又自动创建一次。真实 PostgreSQL 会因为类型已经存在而失败。

修复后只由 `create_table()` 创建一次；降级时在删除表后显式删除 Enum。

这说明迁移验证不能只运行：

```text
alembic heads
```

还要检查从空库生成的完整 SQL，最终再通过真实 PostgreSQL 或 Testcontainers 执行。

## 11. 本地 Worker 入口

查看 Celery 能否发现应用：

```powershell
cd backend
.\.venv\Scripts\celery.exe `
  -A app.infrastructure.task_queue.celery_app:celery_app report
```

Windows 本地启动 Worker 时使用 solo pool：

```powershell
.\.venv\Scripts\celery.exe `
  -A app.infrastructure.task_queue.celery_app:celery_app worker `
  --loglevel=INFO `
  --pool=solo
```

目前尚未注册实际摘要任务，所以启动 Worker 只能验证运行基础。等下一模块实现摘要任务处理后再进行投递测试。

当前 Compose 没有后端 Dockerfile，因此本模块没有增加一个无法构建的 Worker 容器。后续容器化模块会让 API 和 Worker 复用同一后端镜像。

## 12. 测试保护什么

- 任务冻结目标消息和时间。
- 幂等键有唯一约束。
- 尝试次数有数据库检查约束。
- 同一会话只能有一个活动任务。
- 状态机允许和拒绝正确转换。
- Repository 不提交事务。
- Celery 使用 Redis、JSON、late ack 和 prefetch 1。
- Celery 不使用 result backend。
- 迁移连接当前 head，且不会重复创建 Enum。
- Celery CLI 能发现应用配置。

## 13. 面试表达

可以这样说明：

> 我没有使用 FastAPI 的内存后台任务生成长会话摘要，而是先在 PostgreSQL 创建持久化任务单，再通过 Redis Broker 把 job ID 交给 Celery Worker。PostgreSQL 保存任务业务状态、冻结的目标游标、重试次数和幂等键，是唯一事实来源。Celery 使用 late ack 和单任务预取，因此处理逻辑按可能重复执行设计；数据库的幂等键和活动任务部分唯一索引防止重复任务和并发覆盖。

## 14. 小练习

假设任务 A 正在总结会话 1 到消息 60，Worker 执行到一半崩溃，Celery 又投递了一次相同任务。

请回答：

1. 新 Worker 应该接收完整消息正文还是只接收 `job_id`？
2. 新 Worker 应该从哪里读取任务状态和目标消息？
3. 为什么不能直接再创建一条新任务？
4. 如果数据库已经显示 `SUCCEEDED`，新 Worker 应该做什么？

答案要点：只传 job ID；从 PostgreSQL 读取；幂等键和活动任务约束防止重复；已经成功时直接结束，不再次更新摘要。
