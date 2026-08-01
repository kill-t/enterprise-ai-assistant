# 摘要失败分类、延迟重试与指数退避

## 1. 本模块解决什么问题

此前所有摘要异常都会直接进入 `FAILED`：

```text
模型临时超时一次
→ FAILED
→ 任务永远不再执行
```

但失败原因不一样：网络超时可能过一会恢复，冻结目标消息丢失则不会自动恢复。本模块先判断错误是否值得重试，再持久化下一次允许执行的时间。

## 2. 新的失败状态表达

数据库没有增加新的状态枚举，而是复用现有 `PENDING`：

```text
PENDING + next_retry_at 为空
→ 新任务，可以立即执行

PENDING + next_retry_at 在未来
→ 临时失败，正在等待重试

FAILED
→ 永久错误，或者已经耗尽 max_attempts
```

这样等待重试的任务仍属于活动任务，部分唯一索引会阻止同一会话创建第二个重叠摘要任务。

## 3. 哪些错误可以重试

第一版分类：

| 错误 | 是否重试 | 原因 |
|---|---:|---|
| 模型超时 | 是 | 服务或网络可能恢复 |
| API 连接失败 | 是 | 通常是临时基础设施故障 |
| 模型限流 | 是 | 等待后配额可能恢复 |
| Provider 5xx | 是 | 服务端临时异常 |
| 模型返回非法五栏结构 | 是 | 下一次生成可能正确 |
| 摘要版本冲突 | 是 | 重新读取新版本后可以恢复 |
| 冻结消息范围丢失 | 否 | 相同输入再次执行仍会失败 |
| 完整 Prompt 必然超限 | 否 | 等待不会改变 token 数量 |
| Provider 4xx 请求拒绝 | 否 | 通常需要修改配置或请求 |
| 未知代码错误 | 否 | 防止程序缺陷形成重试风暴 |

分类集中在 `SummaryFailurePolicy`，Worker 不直接判断每一种异常类型。

## 4. 为什么未知错误默认不重试

如果把所有异常都设为可重试，一个确定性的代码错误可能变成：

```text
执行失败
→ 立即重试
→ 再次失败
→ 大量任务反复占用 Worker
```

未知错误默认进入 `SUMMARY_UNEXPECTED_ERROR`，需要开发者先看日志定位。只有明确知道可能随时间恢复的错误才能自动重试。

这是“白名单重试”，比“所有错误都重试”更安全。

## 5. 指数退避如何计算

默认配置：

```dotenv
WORKER__SUMMARY_RETRY_BASE_DELAY_SECONDS=30
WORKER__SUMMARY_RETRY_MAX_DELAY_SECONDS=300
```

计算规则：

```text
第 1 次失败：30 秒后
第 2 次失败：60 秒后
第 3 次失败：达到 max_attempts，不再重试
```

通用公式：

```text
delay = min(base_delay × 2^(attempts - 1), max_delay)
```

退避可以避免模型服务故障时所有任务持续高频请求同一个故障点。

## 6. attempts 在什么时候增加

`attempts` 不是投递次数，而是成功领取执行权的次数：

```text
PENDING
→ Worker 原子领取
→ attempts + 1
→ RUNNING
```

重复 Celery 消息没有领取成功，不增加次数。只有真正开始业务执行才消耗一次机会。

默认 `max_attempts = 3`：

- `attempts = 1` 失败：可以等待第二次。
- `attempts = 2` 失败：可以等待第三次。
- `attempts = 3` 失败：进入 `FAILED`。

## 7. Repository 如何防止迟到结果

可重试失败使用条件更新：

```text
WHERE id = job_id
  AND status = RUNNING
  AND attempts = 当前 Worker 的 attempts
  AND attempts < max_attempts
```

更新成功后：

```text
status = PENDING
next_retry_at = 计算后的时间
celery_task_id = null
started_at = null
保存稳定 error_code 和 error_message
```

如果任务已经被其他执行版本修改，UPDATE 返回空，旧 Worker 只能返回 `stale`。

## 8. 为什么领取时还要检查 next_retry_at

即使 Celery 因重复消息提前送达，数据库领取 SQL 仍要求：

```text
next_retry_at 为空
OR
next_retry_at <= 当前时间
```

所以队列消息只能通知 Worker“可以尝试”，最终是否允许执行仍由 PostgreSQL 决定。

## 9. 为什么不直接依赖 Celery retry

项目需要满足服务重启后的业务状态恢复和管理界面展示，因此重试事实必须存在 PostgreSQL：

- 当前失败原因。
- 已经执行几次。
- 下一次何时允许执行。
- 是否永久失败。

Celery 仍然负责运送消息，但不是业务任务状态的唯一来源。

Celery 官方也说明，`acks_late` 仍可能发生重复执行，任务必须保持幂等；Worker 进程异常退出时消息也不一定自动重新排队。因此项目继续使用数据库状态和后续恢复扫描，而不是只相信 Broker。

## 10. 错误信息为什么分日志和数据库

完整异常和堆栈写入日志，用于开发者排错。

数据库只保存：

```text
error_code = SUMMARY_PROVIDER_TIMEOUT
error_message = 摘要模型调用超时
```

不会保存 API 地址、模型原始响应、堆栈或用户敏感正文。

## 11. 数据库迁移

新增字段：

```text
conversation_summary_jobs.next_retry_at
```

新增索引：

```text
(status, next_retry_at)
```

执行：

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head
```

查看当前版本：

```powershell
.\.venv\Scripts\alembic.exe current
```

## 12. 测试保护什么

- 模型超时被识别为可重试错误。
- 非法模型结构可以延迟重试。
- 范围错误和 Prompt 超限永久失败。
- 未知程序错误不会无限重试。
- 退避时间按指数增长并受最大值限制。
- 次数未耗尽时写入 `PENDING + next_retry_at`。
- 达到最大次数后写入 `FAILED`。
- 未到 `next_retry_at` 时不能领取或提前投递。
- 迁移能增加和删除 `next_retry_at`。

## 13. 当前还缺什么

数据库现在能表达“什么时候应该重试”，但还缺少主动扫描者。

下一模块会实现：

```text
周期恢复任务
→ 扫描到期 PENDING
→ 重新投递
→ 扫描超时 RUNNING
→ 恢复或永久失败
```

## 14. 面试表达

> 我没有把所有异常都交给 Celery 无限重试，而是建立失败白名单策略。超时、连接、限流和偶发结构错误会按指数退避写回 PostgreSQL 的 PENDING + next_retry_at；范围丢失、Prompt 超限和未知代码错误进入永久 FAILED。attempts 只在原子领取成功时增加，领取和收尾都匹配当前执行版本。Broker 只负责投递，PostgreSQL 保存可恢复、可查询的真实业务状态。

## 15. 小练习

默认最多执行三次。某任务经历：

```text
第 1 次：模型超时
第 2 次：模型限流
第 3 次：模型仍然超时
```

请回答：

1. 第一次失败后是什么状态？
2. 第二次失败后是什么状态？
3. 第三次失败后为什么不能继续重试？

答案要点：前两次都是带未来 `next_retry_at` 的 `PENDING`；第三次已经达到 `max_attempts=3`，必须进入 `FAILED`，避免无限执行。
