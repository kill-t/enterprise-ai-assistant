# 摘要任务周期恢复与投递租约

## 1. 本模块解决什么问题

上一模块已经可以把临时失败保存成：

```text
PENDING + next_retry_at
```

但仅仅保存时间还不够。如果之后没有用户继续聊天，系统就没有机会再次投递任务；如果 Worker 在领取任务后进程崩溃，数据库还会一直停在 `RUNNING`。

本模块增加一个由 Celery Beat 定时触发的恢复扫描：

```text
每 60 秒扫描一次 PostgreSQL
→ 找出运行超时的 RUNNING
→ 次数未耗尽：恢复成延迟 PENDING
→ 次数已耗尽：标记 FAILED
→ 找出已经到期的 PENDING
→ 领取投递租约
→ 发布给 Redis Broker
```

因此，PostgreSQL 仍是任务状态的唯一事实来源；Redis Broker 即使临时不可用，任务也不会从业务记录中消失。

## 2. 真实调用链

```text
Celery Beat
→ conversation.recover_summary_jobs
→ ConversationSummaryRecoveryService.recover
→ Repository 限量恢复卡死 RUNNING
→ Repository 查询到期 PENDING 的候选 ID
→ ConversationSummaryDispatchService.dispatch_if_due
→ PostgreSQL 原子领取 dispatch_claimed_at
→ commit，让其他扫描器看见租约
→ Redis Broker 发布 job_id
→ PostgreSQL 记录 celery_task_id
```

正常 Worker 的执行链仍然是：

```text
Redis Broker
→ conversation.process_summary_job
→ 原子领取 PENDING：attempts + 1，改成 RUNNING
→ 生成并保存摘要
→ SUCCEEDED / 延迟 PENDING / FAILED
```

恢复扫描不直接生成摘要，它只负责修复任务状态和重新运送消息。

## 3. 为什么 `acks_late` 还不够

项目已经启用 Celery 的：

```text
task_acks_late = true
worker_prefetch_multiplier = 1
```

它们能减少 Worker 提前确认后丢失任务的情况，但不能代替业务恢复机制。Celery 官方文档说明，即使启用 late acknowledgement，执行任务的子进程在某些异常退出场景中，消息仍可能被确认；同时消息队列本身允许重复投递，所以任务仍需保持幂等。

参考：[Celery Tasks - Acknowledgement 与幂等](https://docs.celeryq.dev/en/stable/userguide/tasks.html)

项目因此同时使用：

- Celery 负责消息传输。
- PostgreSQL 记录任务状态、次数和恢复时间。
- Worker 使用条件 UPDATE 防止同一业务任务重复执行。
- 周期扫描修复长期停留的状态。

## 4. 如何判断 RUNNING 已经卡死

默认配置：

```dotenv
WORKER__TASK_TIME_LIMIT_SECONDS=300
WORKER__SUMMARY_RUNNING_TIMEOUT_SECONDS=600
```

判断条件：

```text
status = RUNNING
AND started_at <= 当前时间 - 600 秒
```

恢复阈值必须晚于 Celery 的 300 秒硬超时，否则正常执行中的任务可能被误判为失联。

这里不能只看 `updated_at`，因为其他无关更新可能改变它。`started_at` 明确表示当前执行版本从什么时候开始。

## 5. 卡死任务如何恢复

任务还有执行次数时：

```text
RUNNING
→ PENDING
→ 清除 started_at、celery_task_id 和旧投递租约
→ 设置 next_retry_at
→ 记录 SUMMARY_WORKER_LOST
```

达到 `max_attempts` 时：

```text
RUNNING
→ FAILED
→ 记录 SUMMARY_WORKER_LOST_MAX_ATTEMPTS
```

所以正确状态机是：

```text
PENDING → RUNNING
RUNNING → SUCCEEDED
RUNNING → PENDING     可重试失败或 Worker 失联
RUNNING → FAILED      永久失败或次数耗尽
```

`SUCCEEDED` 和 `FAILED` 都是终态，不能自动重新打开。以后若要支持人工重新执行，应创建一条具有新幂等键的新任务，而不是篡改旧任务历史。

## 6. 为什么恢复必须使用条件 UPDATE

错误做法是先查出卡死记录，再逐条无条件修改。两个恢复扫描器可能同时看到相同任务，并各自恢复一次。

Repository 使用一条带子查询的限量 UPDATE：

```sql
UPDATE conversation_summary_jobs
SET status = 'PENDING', ...
WHERE id IN (
    SELECT id
    FROM conversation_summary_jobs
    WHERE status = 'RUNNING'
      AND started_at <= :stale_before
      AND attempts < max_attempts
    ORDER BY started_at
    LIMIT :batch_size
)
AND status = 'RUNNING'
RETURNING ...;
```

外层再次检查 `status = RUNNING`。即使两个扫描器选择到同一个 ID，也只有仍处于旧状态的一方能完成更新。

每轮默认最多处理 100 条，避免一次扫描长时间锁住大量数据库记录。

## 7. 投递租约是什么

恢复扫描、用户聊天后的即时调度，甚至多个应用实例，都可能同时尝试发布同一个 `PENDING` 任务。

新增字段：

```text
dispatch_claimed_at
```

发布前先执行原子条件 UPDATE：

```text
任务必须是 PENDING
AND 已到 next_retry_at
AND attempts < max_attempts
AND 没有租约，或者旧租约已经超时
```

领取成功后先提交数据库事务，再访问 Broker。其他发布者看到未过期租约时会跳过该任务。

这与租一辆共享单车类似：租约不是任务完成状态，只表示“这段时间由当前发布者负责把消息送到 Broker”。

## 8. 为什么租约会过期

发布者可能在以下位置崩溃：

```text
数据库已经记录租约
→ 进程崩溃
→ 还没向 Broker 发布
```

如果租约永久有效，这个任务再也不会被投递。因此默认两分钟后允许新扫描器接管：

```dotenv
WORKER__SUMMARY_DISPATCH_LEASE_SECONDS=120
```

租约不能短于 60 秒扫描周期，否则扫描抖动或 Broker 延迟可能让下一轮过早接管。

## 9. 为什么 Worker 开始后仍保留投递租约

存在一种很快的并发顺序：

```text
发布方把消息送到 Broker
→ Worker 立即开始，甚至已经完成
→ 发布方才拿到 Broker 返回的 celery_task_id
```

如果 Worker 一领取任务就清除 `dispatch_claimed_at`，发布方会因为匹配不到自己的租约而无法补写 `celery_task_id`。

所以本模块采用：

- Worker 进入 `RUNNING`、`SUCCEEDED` 或永久 `FAILED` 时保留本次租约。
- 发布方仍可凭相同租约补写 `celery_task_id`。
- 任务进入下一次延迟重试时清除旧租约和旧 task ID。

这里保存 task ID 的主要目的不是去重，而是日志和审计关联。真正的执行权仍由数据库状态和 `attempts` 裁决。

## 10. Broker 发布失败会怎样

```text
领取并提交租约
→ Redis Broker 发布失败
→ 按 job_id + claimed_at 释放自己的租约
→ commit
→ 返回 publish_failed
```

任务保持 `PENDING`，后续聊天请求或下一轮恢复扫描可以再次尝试。

如果进程在“Broker 已接收”和“数据库记录 task ID”之间崩溃，系统不能只靠两个独立系统实现严格的 exactly-once。租约过期后可能再次发布，但 Worker 的原子领取会让重复消息跳过业务处理。

未来涉及发送邮件等外部副作用时，还必须让工具调用自身使用幂等键；投递租约不能代替副作用幂等。

## 11. 各层分别负责什么

- Celery Beat：按固定周期发送恢复扫描消息。
- Celery Task：组装异步 Session、Repository 和 Service。
- Recovery Service：规定卡死恢复、候选查询和重新投递流程。
- Dispatch Service：集中处理投递租约、Broker 发布与事务顺序。
- Repository：执行条件 SQL，不自行提交事务。
- PostgreSQL：保存真实状态并裁决并发写入。
- Redis Broker：把轻量 `job_id` 运送给 Worker。

把投递逻辑从任务创建 Service 中抽出来，是因为“新建后立即发布”和“定时扫描后重新发布”必须复用完全相同的并发规则。

## 12. 新增配置

```dotenv
WORKER__SUMMARY_RECOVERY_INTERVAL_SECONDS=60
WORKER__SUMMARY_RUNNING_TIMEOUT_SECONDS=600
WORKER__SUMMARY_DISPATCH_LEASE_SECONDS=120
WORKER__SUMMARY_RECOVERY_BATCH_SIZE=100
```

配置层会拒绝：

- 运行恢复阈值小于或等于 Celery 硬超时。
- 投递租约短于恢复扫描周期。
- 批次大小不在 1 到 1000 之间。

## 13. 数据库迁移

本模块新增：

```text
conversation_summary_jobs.dispatch_claimed_at
索引：(status, dispatch_claimed_at)
```

应用所有未执行迁移：

```powershell
cd <项目目录>\backend
.\.venv\Scripts\alembic.exe upgrade head
```

查看当前版本：

```powershell
.\.venv\Scripts\alembic.exe current
```

查看迁移历史：

```powershell
.\.venv\Scripts\alembic.exe history
```

## 14. 本地如何运行恢复扫描

当前 Docker Compose 只启动 PostgreSQL 和 Redis，Celery Worker 与 Beat 需要分别打开终端运行。

终端一，启动 Worker：

```powershell
cd <项目目录>\backend
.\.venv\Scripts\celery.exe -A app.infrastructure.task_queue.celery_app:celery_app worker --loglevel=INFO --pool=solo --queues=conversation-summary
```

终端二，启动 Beat：

```powershell
cd <项目目录>\backend
.\.venv\Scripts\celery.exe -A app.infrastructure.task_queue.celery_app:celery_app beat --loglevel=INFO
```

Windows 本地使用 `--pool=solo`，便于稳定调试。以后容器化交付时会把 API、Worker 和 Beat 分成三个进程写入 Docker Compose，但它们仍共享同一套应用代码和 PostgreSQL 任务表。

## 15. 测试保护什么

- 状态机允许 `RUNNING → PENDING`，并把 `FAILED` 视为终态。
- 恢复阈值必须晚于 Celery 硬超时。
- 卡死任务通过限量条件 UPDATE 原子恢复。
- 次数耗尽的卡死任务进入永久失败。
- 到期 PENDING 会进入投递流程。
- 多个发布者必须先原子领取租约。
- 租约在 Broker 发布前已经提交。
- Broker 故障后会释放当前租约。
- Celery 能发现周期任务和 Beat 配置。
- Alembic 可以增加和删除投递租约字段。

## 16. 常见错误

- 认为 `acks_late` 等于业务 exactly-once。
- 只设置 `next_retry_at`，却没有定时扫描者。
- 恢复阈值比任务硬超时更短，误伤正常任务。
- 多个实例查询候选后直接发布，没有竞争投递权。
- 领取租约但不先提交，其他实例仍看不到。
- Broker 失败后不释放租约，造成无意义等待。
- 恢复时不限制批次，一次锁住大量数据库记录。
- 把 Celery task ID 当成业务幂等机制。

## 17. 面试表达

> 我把摘要任务状态放在 PostgreSQL，而不是只依赖 Celery。Beat 周期扫描超时 RUNNING 和到期 PENDING；恢复使用限量条件 UPDATE，发布前再原子领取短期投递租约并先提交。Broker 故障会释放租约，发布者崩溃则由租约过期接管。由于数据库和 Broker 之间不存在统一事务，我不声称 exactly-once，而是接受消息可能重复，并用 Worker 原子领取、attempts 执行版本和后续工具幂等来保证业务安全。

## 18. 小练习

请按顺序解释下面场景：

```text
扫描器 A 领取了任务的投递租约并提交
→ A 在访问 Redis 前崩溃
→ 一分钟后扫描器 B 再次扫描
→ 两分钟后扫描器 B 再次扫描
```

请回答：

1. 一分钟后 B 为什么不能发布？
2. 两分钟后 B 为什么可以接管？
3. 如果 A 实际已经发布，只是没记录 task ID，为什么重复消息通常不会重复生成摘要？

答案要点：租约默认两分钟内有效；过期后允许重新领取；Worker 必须先把 `PENDING` 原子改成 `RUNNING`，只有成功领取执行权的消息才会真正调用摘要处理器。
