# 文档任务投递租约与周期恢复

## 1. 这一小步补上什么

RAG-3A 已经能执行一个给定的文档任务，但上传接口只创建 PostgreSQL `PENDING` 记录，没有
自动把 `job_id` 送给 Celery Worker。如果 Redis Broker 临时不可用，或者 Worker 执行到
一半进程退出，任务也缺少自动恢复入口。

本模块完成 RAG-3B：

```text
上传提交文档与任务
→ 领取投递租约
→ Celery 发布 job_id
→ Worker 原子领取并入库

Celery Beat 周期扫描
├─ 到期 PENDING → 重新投递
├─ 超时 RUNNING 且仍可重试 → PENDING
└─ 超时 RUNNING 且次数耗尽 → FAILED
```

没有新增数据库迁移。`celery_task_id`、`dispatch_claimed_at`、`started_at`、
`next_retry_at` 和 `attempts` 已在 RAG 数据地基阶段预留。

## 2. 为什么数据库提交与 Broker 发布不能放进一个普通事务

PostgreSQL 和 Redis 是两个独立系统：

```text
PostgreSQL COMMIT
Redis/Celery publish
```

普通数据库事务不能让两者严格同时成功。可能出现：

### 先发布、后提交

```text
Celery 已收到 job_id
→ Worker 立刻查询 PostgreSQL
→ 上传事务还没提交
→ Worker 找不到任务
```

### 先提交、后发布

```text
PostgreSQL 已有 PENDING 任务
→ Redis 临时不可用
→ 发布失败
```

本项目选择第二种，因为 PENDING 任务是可查询、可恢复的事实。恢复扫描以后可以再次发布；
而没有数据库记录的 Broker 消息无法可靠补全业务数据。

这种做法类似轻量 Transactional Outbox：业务任务表本身同时承担待发布记录，不额外建立
通用 Outbox 表。

## 3. 上传为什么不能因为调度失败而返回失败

上传成功的核心事实是：

- 原文件已经在 MinIO。
- 文档和持久化任务已经在 PostgreSQL。

它们提交后，即使 Celery 调度异常，也不能返回“上传失败”。否则用户再次上传会遇到
SHA-256 重复冲突，却不知道第一次其实已经成功。

`DocumentService` 的顺序是：

```text
MinIO put_object
→ PostgreSQL 创建文档与任务
→ COMMIT
→ _dispatch_ingestion_safely(job_id)
→ 返回 202 Accepted
```

调度异常只回滚调度阶段尚未提交的 Session 状态并记录日志。数据库 PENDING 任务保留，
等待 Beat 扫描。

## 4. 投递租约解决什么并发问题

API 上传完成后会尝试发布，Celery Beat 也会周期扫描。如果两者同时看到一个 PENDING：

```text
API 发布一次
Beat 又发布一次
```

Worker 虽然有原子领取保护，不会重复入库，但无意义的重复消息会增加 Broker 和日志噪音。

发布前执行一条条件 UPDATE：

```text
WHERE status = PENDING
  AND next_retry_at 已到期
  AND attempts < max_attempts
  AND dispatch_claimed_at 为空或已经过期

SET dispatch_claimed_at = 当前时间
```

只有拿到 `RETURNING` 记录的发布者拥有投递权。租约必须先 COMMIT，再访问 Broker，其他
发布者才能看见它。

默认租约 120 秒，恢复扫描间隔 60 秒。配置校验要求租约不能短于扫描周期，否则一次正常
发布还没完成，下一轮扫描就可能过早接管。

## 5. Broker 成功或失败后怎样处理

### 发布成功

```text
Celery 接收 job_id
→ 返回 celery_task_id
→ 按 job_id + dispatch_claimed_at 写回任务表
```

写回时匹配租约时间，迟到的旧发布者不能覆盖新发布者记录。

### 发布失败

```text
Redis 连接失败
→ release_dispatch_claim
→ dispatch_claimed_at = NULL
→ 保持 PENDING
```

下一轮 Beat 可以立即重新领取。

### Broker 成功，但进程在写回 task ID 前崩溃

这时消息可能已经存在，但数据库仍没有 `celery_task_id`。租约过期后系统可能再次发布。
项目不声称严格 exactly-once，而是接受消息可能重复，再依靠 Worker 的 PENDING → RUNNING
原子领取保证业务只执行一次。

`celery_task_id` 用于日志关联，不是业务幂等锁。

## 6. 恢复扫描怎样处理卡死 RUNNING

Celery Beat 每 60 秒触发：

```text
knowledge.recover_document_ingestion_jobs
```

默认把运行超过 900 秒的 RUNNING 视为失联。这个阈值必须晚于 Celery 300 秒硬超时，避免
仍在正常解析 PDF 的任务被提前重排。

### 仍有次数

```text
job: RUNNING → PENDING
document: PROCESSING → PENDING
next_retry_at = 当前时间 + 退避延迟
error_code = DOCUMENT_WORKER_LOST
```

### 次数耗尽

```text
job: RUNNING → FAILED
document: PROCESSING → FAILED
error_code = DOCUMENT_WORKER_LOST_MAX_ATTEMPTS
```

任务和文档状态在同一个事务提交，不能出现任务已经 PENDING、前端却一直看到文档
PROCESSING。

## 7. 为什么恢复 SQL 要有条件和批次上限

不能先查出一批 RUNNING，再逐条无条件修改。多个 Beat 实例可能同时扫描，查询结果很快
过期。

Repository 使用：

```text
带状态、started_at、attempts 条件的子查询
→ ORDER BY started_at
→ LIMIT batch_size
→ 单条 UPDATE ... RETURNING
```

默认每轮最多处理 100 条，避免积压任务一次占满数据库。条件 UPDATE 让并发扫描器即使看到
相同候选，也只能更新仍满足条件的记录。

## 8. 为什么扫描后不一直开着事务发布

恢复 Service 的顺序：

```text
事务 1：恢复超时任务，并同步文档状态
COMMIT

事务 2：读取到期 PENDING 的 job_id
COMMIT，释放只读事务

逐个调用 DispatchService
```

Redis Broker 网络等待期间不保留扫描查询事务。每个任务的投递租约由 DispatchService 使用
短事务独立处理。

## 9. 队列为什么分开

当前有两个队列：

```text
conversation-summary：会话摘要
document-ingestion：文档解析、分块、Embedding、恢复扫描
```

文档任务可能处理较大 PDF，耗时明显长于摘要。分开队列后，容器化阶段可以独立调整文档
Worker 数量，不让大量入库任务阻塞会话派生任务。

Windows 本地为了简单，可以让一个 solo Worker 同时消费两个队列。

## 10. Windows 本地怎样启动

先启动 PostgreSQL、Redis 和 MinIO，再在 `backend` 目录打开三个终端。

终端一，API：

```powershell
.\.venv\Scripts\python.exe -m app.local_server
```

终端二，Worker：

```powershell
.\.venv\Scripts\celery.exe -A app.infrastructure.task_queue.celery_app:celery_app worker --loglevel=INFO --pool=solo --queues=conversation-summary,document-ingestion
```

终端三，Beat：

```powershell
.\.venv\Scripts\celery.exe -A app.infrastructure.task_queue.celery_app:celery_app beat --loglevel=INFO
```

Windows 使用 `--pool=solo` 便于稳定调试。Worker 真正执行任务；Beat 只负责按计划发布恢复
扫描消息，不能用 Beat 代替 Worker。

## 11. 关键代码入口

- Celery 发布适配器：
  `backend/app/infrastructure/task_queue/document_ingestion_dispatcher.py`
- 投递租约 Service：
  `backend/app/modules/knowledge/service/document_ingestion_dispatch_service.py`
- 周期恢复 Service：
  `backend/app/modules/knowledge/service/document_ingestion_recovery_service.py`
- 任务租约和恢复 SQL：
  `backend/app/modules/knowledge/repository/document_ingestion_job_repository.py`
- 文档状态同步：
  `backend/app/modules/knowledge/repository/document_index_repository.py`
- 上传后调度：
  `backend/app/modules/knowledge/service/document_service.py`
- Worker 与 Beat 入口：
  `backend/app/modules/knowledge/tasks/document_ingestion_task.py`

## 12. 测试保护了什么

`tests/test_document_ingestion_recovery.py` 覆盖：

- 投递租约检查状态、重试时间、次数和租约过期。
- 租约提交发生在 Broker 发布之前。
- Broker 失败释放租约。
- Celery 消息只携带 job ID，并进入独立队列。
- 卡死任务使用带 LIMIT 的原子 UPDATE。
- 恢复时任务和文档状态同步。
- 恢复扫描统计到期候选与成功发布数。
- Celery 注册恢复任务、路由和 Beat 周期。
- 恢复阈值与租约时间配置不能互相冲突。

`tests/test_document_upload.py` 还验证：

- 上传事务提交后才开始投递。
- 调度内部异常不覆盖已成功上传的结果。

## 13. 常见错误怎样排查

### 文档一直 PENDING

```text
检查 Worker 是否消费 document-ingestion
→ 检查 Redis Broker
→ 查看 dispatch_claimed_at
→ 查看 celery_task_id
→ 查看 next_retry_at 是否尚未到期
```

### 文档一直 PROCESSING

```text
检查 Worker 日志是否仍在处理
→ 检查 started_at
→ 确认 Beat 是否启动
→ 确认 900 秒恢复阈值是否已经到达
```

### Beat 在运行但任务不恢复

Beat 只发送周期消息，仍需要 Worker 消费 `document-ingestion` 队列并执行恢复 Task。

## 14. 面试表达

> PostgreSQL 和 Redis Broker 之间没有统一事务，所以我先提交 PENDING 文档任务，再用短期
> 投递租约发布 job ID。Broker 失败会释放租约，API 仍返回上传成功；Beat 周期扫描到期
> PENDING 和超时 RUNNING。失联恢复同时修改任务和文档状态，并使用限量条件 UPDATE
> 防止并发扫描。系统不假装 exactly-once，而是用可能重复投递、Worker 原子领取和
> attempts 执行版本实现最终可靠和业务幂等。

## 15. 小练习（15–30 分钟）

给恢复测试增加第三个到期任务，让 `dispatch_if_due` 依次返回：

```text
dispatched
publish_failed
dispatched
```

要求最终结果：

```text
dispatch_candidates = 3
dispatched = 2
```

答案要点：候选数表示扫描发现多少到期任务，成功数只统计真正返回 `dispatched` 的任务；
一次 Broker 失败不能让整轮统计假装全部成功。
