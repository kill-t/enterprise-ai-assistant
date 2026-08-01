# 摘要 Worker 原子领取与重复执行保护

## 1. 本模块解决什么问题

上一个模块已经能把摘要任务写入 PostgreSQL，并把 `job_id` 投递给 Celery。现在补上真正接收消息的 Worker 入口。

消息队列可能重复投递，因此不能认为“Worker 收到一次消息，就只会执行一次”。本模块保证：

```text
两个 Worker 同时收到相同 job_id
→ 都尝试执行同一条条件 UPDATE
→ 只有一个 Worker 能把 PENDING 改为 RUNNING
→ 另一个 Worker 更新不到数据，直接跳过
```

本模块只完成可靠执行框架，尚未调用大模型生成摘要。未接入真实处理器前，任务会明确记录为失败，不会把“什么都没做”伪装成成功。

## 2. 真实调用链

```text
Redis Broker 中的 Celery 消息
→ process_conversation_summary_job(job_id)
→ asyncio.run 创建异步执行环境
→ SessionLocal 创建数据库 Session
→ ConversationSummaryWorkerService.execute
→ ConversationSummaryJobRepository.claim_pending
→ PostgreSQL 条件 UPDATE：PENDING → RUNNING，attempts + 1
→ commit，公开任务领取结果
→ ConversationSummaryProcessor.process
→ 成功：按 attempts 标记 SUCCEEDED
→ 失败：按 attempts 标记 FAILED
→ commit
```

## 3. 为什么不能先查询再修改

容易产生竞争的写法：

```text
Worker A：SELECT，看到 PENDING
Worker B：SELECT，也看到 PENDING
Worker A：UPDATE RUNNING，开始执行
Worker B：UPDATE RUNNING，也开始执行
```

如果以后执行的是“发送邮件”，这可能导致同一封邮件发送两次。

本模块把检查和修改放进同一条 SQL：

```sql
UPDATE conversation_summary_jobs
SET status = 'RUNNING', attempts = attempts + 1
WHERE id = :job_id
  AND status = 'PENDING'
  AND attempts < max_attempts
RETURNING ...;
```

数据库会原子地处理这条语句。第一个 Worker 修改成功后，后续 Worker 的 `status = PENDING` 条件已经不成立，因此拿不到任务。

## 4. 为什么领取后立刻提交

Worker 领取任务时使用一个数据库事务。如果一直不提交，其他连接可能仍然看不到已经变成 `RUNNING` 的状态。

所以执行顺序是：

```text
领取任务
→ commit
→ 执行耗时业务
→ 保存最终状态
→ commit
```

这也避免在调用大模型的整个等待期间一直占用一个长数据库事务。

代价是：领取提交后进程可能崩溃，数据库会暂时留下 `RUNNING`。后续模块需要增加“超时 RUNNING 任务恢复”，根据 `started_at` 判断是否重新进入待处理状态。

## 5. attempts 为什么不仅是计数器

`attempts` 除了记录执行次数，也充当简单的执行版本号。

例如：

```text
Worker A 领取任务：attempts = 1，随后卡住
恢复机制重新投递
Worker B 领取重试：attempts = 2，并处理成功
Worker A 很晚才返回，尝试提交 attempts = 1 的结果
```

Worker A 的收尾 SQL 必须同时满足：

```text
status = RUNNING
attempts = 1
```

如果数据库已经是新执行版本，旧 Worker 更新不到记录，只返回 `stale`，不能覆盖较新的状态。这类版本校验也叫“防止过期执行者写入”。

## 6. 为什么异常正文只写日志

处理器可能抛出包含内部地址、模型响应或其他敏感信息的异常。数据库面向管理接口和前端展示，因此只保存稳定信息：

```text
error_code = SUMMARY_PROCESSING_FAILED
error_message = 会话摘要任务处理失败
```

处理器失败时，Worker 会先回滚同一 Session 中尚未提交的摘要半成品，再用一个干净事务保存失败状态。完整堆栈只写入服务日志，开发者仍能定位问题，但不会把内部异常直接暴露给用户。

## 7. Celery 同步入口如何调用异步代码

当前 Celery Task 是普通同步函数，而 SQLAlchemy 使用异步 Session：

```text
同步 Celery Task
→ asyncio.run(...)
→ 异步 Worker Service
→ 异步 SQLAlchemy
```

`asyncio.run` 会为本次任务创建事件循环，执行结束后关闭。项目的数据库 Engine 是模块级对象，因此任务结束时主动 `engine.dispose()`，避免连接池里的异步连接被下一个新事件循环错误复用。

这是一种易于理解的第一版桥接方式。以后如果 Worker 吞吐量变大，可以再评估长期运行的事件循环或原生异步任务执行方案。

## 8. 各层分别负责什么

- Celery Task：解析 `job_id`，建立同步到异步的入口，组装依赖。
- Worker Service：规定领取、执行、成功和失败的业务流程。
- Processor：只负责“如何真正生成并保存摘要”，可以被测试替身替换。
- Repository：只生成和执行数据库语句，不自己提交事务。
- PostgreSQL：保存唯一可信的任务状态，并裁决谁获得执行权。

把处理器抽成接口后，测试无需启动 Celery、Redis 或真实大模型，就能验证任务状态机。

## 9. 当前占位处理器为什么主动失败

如果占位处理器什么都不做就返回，Worker 会把任务标成 `SUCCEEDED`，但数据库里没有任何摘要，这是错误成功。

因此 `DeferredConversationSummaryProcessor` 主动抛出异常，让任务保存为 `FAILED`。下一模块接入真实摘要处理器后，会在 Celery 任务组装处替换它。

## 10. 测试保护什么

- 领取必须是一条带状态和最大次数条件的原子 UPDATE。
- 成功收尾必须匹配当前 `attempts`。
- 重复、已领取或已完成任务不能再次调用处理器。
- 领取结果在处理前提交。
- 处理成功后保存 `SUCCEEDED`。
- 处理失败后先回滚半成品，再保存安全的错误码与提示。
- 迟到 Worker 不能覆盖较新的执行状态。
- Celery 能发现 Dispatcher 使用的任务名 `conversation.process_summary_job`。

本模块新增 7 个测试。

## 11. 常见错误

- 用 `SELECT` 后再无条件 `UPDATE`，导致并发重复执行。
- 只依赖 Celery `task_id` 去重，不检查数据库状态。
- 领取任务但不先提交，导致状态不可见和长事务。
- 收尾只按任务 ID 更新，允许迟到 Worker 覆盖新状态。
- 把异常原文保存并返回给前端，泄露内部信息。
- 占位处理器直接返回，制造没有摘要正文的假成功。

## 12. 面试表达

可以这样说明：

> Celery 提供至少一次语义时，业务侧必须防重复。我没有只依赖 task ID，而是让 Worker 使用 PostgreSQL 条件 UPDATE 原子地把 PENDING 改为 RUNNING，只有 RETURNING 拿到记录的 Worker 才执行。领取事务先提交，避免长事务。最终状态更新还匹配 attempts，把它作为执行版本，防止迟到 Worker 覆盖新重试。完整异常只进入日志，数据库只保存稳定错误码和安全提示。

## 13. 小练习

请用自己的话解释下面的结果：

```text
Worker A 和 Worker B 同时收到同一个 job_id。
Worker A 的 claim_pending 返回任务记录。
Worker B 的 claim_pending 返回 None。
```

回答三个问题：

1. 为什么 Worker B 不能继续调用处理器？
2. 为什么 Worker A 要在调用处理器前先 commit？
3. 如果 Worker A 在处理期间崩溃，数据库会留下什么状态，未来还需要补什么机制？

答案要点：B 没获得数据库裁决的执行权；A 提交后其他 Worker 才能看见 RUNNING，且避免把耗时处理放进长事务；崩溃会留下 RUNNING，后续需要超时检测和恢复机制。
