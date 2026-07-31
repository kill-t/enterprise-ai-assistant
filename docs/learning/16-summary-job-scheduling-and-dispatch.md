# 摘要任务触发与提交后可靠投递

## 1. 本模块做了什么

上一个模块只有任务表和 Celery 配置。本模块把“什么时候创建任务”和“如何投递任务”接到助手回复成功后的调用链。

完整流程：

```text
助手回复保存成功
→ 查询当前活动摘要任务
→ 读取尚未被摘要覆盖的已完成消息
→ 计算未摘要历史 token
→ 超过 summary_trigger_tokens
→ PostgreSQL 创建 PENDING 任务
→ commit
→ Celery 投递 job_id
→ 记录 celery_task_id
→ commit
```

本模块仍然不执行摘要生成。Worker 收到的任务名已经确定，但实际任务函数会在下一模块实现。

## 2. 为什么先提交数据库再投递 Celery

错误顺序：

```text
先向 Celery 投递 job_id
→ Worker 立即收到任务
→ Worker 查询 PostgreSQL
→ 数据库任务还没有提交
→ Worker 找不到任务
```

正确顺序：

```text
创建数据库任务
→ commit
→ 再投递 job_id
```

这样 Worker 收到消息时，任务单已经对其他数据库连接可见。

## 3. PostgreSQL 和 Redis 为什么不能一起回滚

这里涉及两个独立系统：

```text
PostgreSQL 事务
Redis / Celery Broker 投递
```

普通 SQLAlchemy 事务只能控制 PostgreSQL，不能让 Redis 自动参加同一个事务。

可能发生：

```text
PostgreSQL commit 成功
→ Redis 投递失败
```

系统不能假装数据库也失败了，因为任务记录已经真实提交。也不应该删除任务记录，否则“需要生成摘要”这件事会消失。

当前方案使用任务表作为轻量 Outbox：

```text
status = PENDING
celery_task_id = null
```

这个状态明确表示：

> 业务任务已经创建，但尚未确认投递成功。

后续请求或恢复扫描可以再次投递。

## 4. 为什么摘要投递失败不回滚聊天

助手回复和摘要任务的业务重要性不同：

```text
助手回复
→ 用户当前请求的核心结果

滚动摘要
→ 可以延迟生成的派生数据
```

助手回复已经保存在 PostgreSQL 后，即使 Redis Broker 不可用：

- 回复仍然返回给用户。
- 原始消息仍然完整保存。
- 当前对话仍可按 Token 裁剪继续工作。
- 摘要任务保留为 PENDING，之后重新投递。

因此摘要系统故障不应该把一次已经成功的聊天变成失败。

## 5. 什么时候触发摘要任务

配置：

```text
CONVERSATION_MEMORY__SUMMARY_TRIGGER_TOKENS=3000
```

触发规则：

```text
未摘要历史 token > 3000
→ 创建任务

未摘要历史 token <= 3000
→ 不创建任务
```

如果已经存在 `PENDING` 或 `RUNNING` 任务，不创建第二条活动任务。

## 6. 为什么只冻结到最近助手回答

历史可能暂时是：

```text
user：问题 1
assistant：回答 1
user：问题 2，但尚未回答
```

任务目标只能冻结到“回答 1”，不能冻结到未完成的问题 2。

否则摘要可能保存了一个没有回答的半轮对话，后续上下文构建也更难保持完整轮次。

## 7. 已有任务如何处理

### 已经投递

```text
status = PENDING
celery_task_id != null
```

不重复投递，等待 Worker 处理。

### 正在运行

```text
status = RUNNING
```

不创建新任务，也不重复投递。

### 数据库已创建但投递失败

```text
status = PENDING
celery_task_id = null
```

再次尝试投递同一个 job ID，不创建新任务。

## 8. Celery 消息包含什么

Dispatcher 使用：

```text
task name：conversation.process_summary_job
args：[job_id]
task_id：job_id
queue：conversation-summary
```

消息中不包含：

- 用户消息正文。
- 旧摘要正文。
- 用户身份信息。
- 数据库连接配置。

Celery 官方 Calling API 支持通过 `send_task()` 按任务名投递未在当前进程注册的任务，并允许传入 `task_id` 等执行选项。

相同 `task_id` 主要用于日志关联，不能把它当作 Broker 去重保证。真正去重仍要依赖 PostgreSQL 状态和 Worker 的原子领取。

## 9. 为什么使用 asyncio.to_thread

Celery 的 `send_task()` 是同步调用，而 FastAPI 会话 Service 是异步代码。

如果直接调用：

```python
celery_app.send_task(...)
```

Broker 网络连接可能阻塞 FastAPI 事件循环，使同一进程中的其他请求无法及时运行。

Dispatcher 使用：

```python
await asyncio.to_thread(celery_app.send_task, ...)
```

把同步投递放到线程中执行。当前请求仍会等待本次快速投递结果，但不会占住事件循环；同时关闭 publish retry，使 Broker 不可用时尽快留下 PENDING 状态。

## 10. 真实调用链

```text
ConversationService._generate_assistant_response
→ 保存 COMPLETED 助手消息
→ commit
→ 写入 Redis 最近消息（可降级）
→ ConversationSummaryJobService.maybe_schedule
→ 查询活动任务
→ 查询摘要游标后的已完成消息
→ TokenCounter 计算未摘要历史
→ 创建 ConversationSummaryJobEntity
→ commit
→ CelerySummaryJobDispatcher.dispatch
→ 保存 celery_task_id
→ commit
```

任何摘要调度内部异常都会被 `_schedule_summary_safely()` 捕获并记录中文日志，不会覆盖已经成功的助手消息。

## 11. 当前还缺什么

任务现在只能被创建和投递，还没有实际消费者。

下一模块需要实现：

```text
Celery Task 接收 job_id
→ PostgreSQL 原子领取 PENDING 任务
→ PENDING 改为 RUNNING
→ 已经 RUNNING / SUCCEEDED 时直接结束
→ 调用后续摘要处理 Service
→ 更新 SUCCEEDED 或 FAILED
```

摘要 LLM 生成和正文更新还会继续小步拆分，不会和任务领取一次完成。

## 12. 测试保护什么

- 未超过阈值时不创建任务。
- 等于阈值时也不创建任务。
- 只冻结到最近完整助手回答。
- 数据库 commit 发生在 Celery dispatch 之前。
- 投递成功后保存 celery_task_id。
- Broker 失败时保留 PENDING 任务。
- 未投递 PENDING 任务能够重新投递。
- 已投递或 RUNNING 任务不会重复投递。
- Celery 消息只携带 job ID。
- 摘要调度故障不影响已成功聊天。
- 模型失败时不触发摘要任务。

本模块完成后共有 90 个测试通过。

## 13. 面试表达

可以这样说明：

> PostgreSQL 和 Redis Broker 不能由普通数据库事务原子提交，所以我让摘要任务表同时承担轻量 Outbox。系统先提交 PENDING 任务，再投递 job ID；投递失败时保留 celery_task_id 为空的 PENDING 记录，后续可以重新投递。助手回复是核心结果，摘要是可延迟派生数据，因此摘要调度失败不会回滚聊天。Dispatcher 通过 asyncio.to_thread 隔离 Celery 的同步投递，消息只携带 job ID，敏感正文仍保留在 PostgreSQL。

## 14. 小练习

按顺序分析下面的故障：

```text
1. 助手回复提交成功
2. 摘要任务提交成功
3. Celery 投递成功
4. 保存 celery_task_id 前进程崩溃
```

请回答：

1. 用户的助手回复是否应该失败？
2. 数据库任务是什么状态？
3. 下次可能发生什么重复操作？
4. 为什么 Worker 仍然必须检查数据库状态？

答案要点：回复不失败；数据库中是 celery_task_id 为空的 PENDING；任务可能使用相同 job ID 再次投递；相同 Celery task ID 不是可靠去重，Worker 必须原子领取 PENDING 任务，后来的重复消息看到 RUNNING 或 SUCCEEDED 后直接结束。
