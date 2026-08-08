# 流式取消、断线终态与原子状态转换

## 1. 本模块解决什么问题

流式聊天开始时已经向 PostgreSQL 写入：

```text
user = COMPLETED
assistant = PENDING
```

正常完成会把助手消息更新为 `COMPLETED`，模型失败会更新为 `FAILED`。但用户停止请求或浏览器连接断开时，Python 会终止正在消费的异步流。如果 Service 不处理这个终止信号，助手消息可能长期停在 `PENDING`。

本模块增加独立终态：

```text
PENDING
├─ COMPLETED：模型正常完成
├─ FAILED：模型或协议失败
└─ CANCELLED：用户停止或连接断开
```

取消时保留已经发送的部分正文，但不把它放入 Redis 或摘要。

## 2. 完整调用链

```text
未来的 Vue AbortController / 浏览器断线
→ FastAPI StreamingResponse 停止消费
→ SSE 生成器 finally 调用 aclose()
→ ConversationService 收到 GeneratorExit 或 CancelledError
→ aclosing 关闭 Provider 网络流
→ MessageRepository 条件 UPDATE
→ PENDING 原子更新为 CANCELLED
→ 提交事务
→ 继续向上抛出取消信号
```

这里有两个不同但相关的 Python 信号：

- `GeneratorExit`：外部对异步生成器调用 `aclose()`。
- `asyncio.CancelledError`：运行该请求的 asyncio Task 被取消。

它们都不是普通模型异常，不能转换成 `FAILED`。

## 3. 为什么普通 except Exception 捕获不到取消

模型错误处理使用：

```python
except Exception:
    ...
```

这会捕获超时、SDK 异常和大多数业务运行错误，但不会捕获取消信号。`CancelledError` 和 `GeneratorExit` 都属于更高层的控制流异常。

这是合理的默认行为：如果底层把取消吞掉，用户点击停止后模型请求可能继续运行。

当前 Service 单独处理：

```python
except (asyncio.CancelledError, GeneratorExit):
    await 保存取消终态
    raise
```

最后的 `raise` 非常重要：数据库状态保存完成后，取消仍要继续向上传播，FastAPI 和模型 SDK 才会真正结束工作。

## 4. 为什么 CANCELLED 不能直接使用 FAILED

`FAILED` 表示系统本来想完成回答，但模型、网络或协议出错了。

`CANCELLED` 表示这次生成被主动停止，或者承载它的连接提前结束。两者的业务含义不同：

| 状态 | 是否系统故障 | 用户界面建议 | 是否可原位重试 |
|---|---|---|---|
| FAILED | 通常是 | 显示安全错误和重试 | 是 |
| CANCELLED | 不一定 | 显示“生成已停止”和重新生成 | 是 |

区分状态能让日志指标、前端提示和面试中的状态机设计更加准确。

## 5. 为什么外层 try 必须覆盖 started

如果代码结构是：

```python
yield started
try:
    调用 Provider
except 取消:
    保存 CANCELLED
```

客户端刚收到 `started` 就断开时，生成器仍停在 `yield started`，还没有进入 `try`，因此取消清理不会执行。

当前结构是：

```python
try:
    yield started
    调用 Provider
except 取消:
    保存 CANCELLED
```

所以从 `started` 发出开始，整个可见流都处于取消保护范围内。

## 6. 为什么必须使用原子条件 UPDATE

下面的“先读再写”存在竞争：

```text
任务 A 读取 status = PENDING
任务 B 保存 status = COMPLETED
任务 A 再保存 status = CANCELLED
```

最终会把已经成功的消息错误覆盖成取消。

Repository 现在使用一条 SQL：

```sql
UPDATE messages
SET status = 'CANCELLED', ...
WHERE id = :message_id
  AND status = 'PENDING'
RETURNING id;
```

检查状态和更新状态由 PostgreSQL 在同一条语句中完成：

- 返回 ID：本次成功完成 `PENDING → CANCELLED`。
- 没有返回：消息已经是 `COMPLETED`、`FAILED` 或 `CANCELLED`，Service 不能覆盖它。

这就是“原子状态转换”：其他请求不能插入“检查”和“更新”中间改变结果。

## 7. 取消时保存哪些数据

取消成功后保存：

- `status = CANCELLED`
- 已经发给用户的部分 `content`
- `error_code = GENERATION_CANCELLED`
- `error_message = 回答生成已取消`
- `finished_at`

不保存模型 token 使用量，因为当前 Provider 没有完成并返回可靠的最终 usage。

取消消息不会进入：

- Redis 最近完成消息缓存。
- 下一次模型历史。
- 会话滚动摘要。

这些位置只使用 `COMPLETED` 消息，避免半截回答污染后续上下文。

## 8. 数据库事务在哪里结束

Repository 只执行条件 UPDATE，不自行提交。Conversation Service 随后调用统一 `_commit()`：

```text
Repository 执行 UPDATE
→ Service commit
→ 数据库成为取消终态的事实来源
```

取消后连接已经结束，Service 不再产生业务事件，因此没有必要为了构造响应而修改内存 ORM 对象。Repository 的条件 SQL 也不会让内存对象参与并发判断。

即使 UPDATE 因消息已有终态而没有命中，Service 也会提交一次，用于明确结束本次事务。

如果取消状态提交失败：

- 记录中文异常日志。
- 回滚当前数据库事务，避免留下不可继续使用的 Session。
- 不用数据库异常覆盖原始取消信号。
- 原始取消继续向上抛出。

数据库不可用时仍可能留下 `PENDING`，以后需要通过超时扫描修复；取消清理只能尽力，不能在数据库故障时保证成功。

## 9. 为什么 completed 附近还要防竞争

可能发生：

```text
Service 已把消息提交为 COMPLETED
→ completed 帧正在发送
→ 客户端立刻断开
→ 生成器收到关闭信号
```

此时取消清理仍可能被调用，但条件 UPDATE 要求数据库状态是 `PENDING`，因此不会覆盖 `COMPLETED`。

同样，它也不会覆盖已经持久化的 `FAILED`。

## 10. 为什么取消消息允许原位重试

取消和失败消息都已经有稳定的助手消息 ID。用户点击重新生成时，系统复用原消息：

```text
CANCELLED / FAILED
→ 清空旧部分正文和错误
→ PENDING
→ 使用原用户问题重新调用模型
→ COMPLETED / FAILED
```

这样不会为同一个问题不断创建新的助手气泡，也符合此前确定的原位重试规则。

`PENDING` 和 `COMPLETED` 仍然拒绝重试，防止重复点击造成并发生成。

## 11. 为什么没有新增 cancelled SSE 事件

用户主动中止 `fetch` 或浏览器断线后，HTTP 连接已经不可用，即使服务器生成 `cancelled` 帧也无法可靠送达。

因此取消终态首先保存在 PostgreSQL。前端停止后可以立即在本地把气泡显示为“已停止”，重新加载会话时再以数据库状态为准。

如果以后由服务器主动取消仍保持连接，可以再评估增加新的 SSE 终点事件；当前没有提前扩展协议。

## 12. PostgreSQL 枚举迁移

Python 增加 `MessageStatus.CANCELLED` 还不够，数据库中的 `messagestatus` 枚举也必须允许 `CANCELLED`。

迁移升级执行：

```sql
ALTER TYPE messagestatus ADD VALUE IF NOT EXISTS 'CANCELLED';
```

在项目中执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

PostgreSQL 不能直接删除一个 enum 值。降级迁移会重建旧枚举，并把已有 `CANCELLED` 数据兼容映射为 `FAILED`。

## 13. 测试保护什么

- `started` 后立即关闭：空助手占位变为 CANCELLED，Provider 不会启动。
- 一个 delta 后关闭：部分正文被保存，Provider 被关闭。
- asyncio Task 取消：先保存 CANCELLED，再继续抛出 `CancelledError`。
- 取消状态写入失败：回滚事务，但不能吞掉原始取消信号。
- completed 附近关闭：不能覆盖已经成功的终态。
- Repository SQL 同时包含消息 ID 和 `status = PENDING` 条件。
- 条件 UPDATE 未命中时报告 False。
- FAILED 和 CANCELLED 都可原位重试。
- PENDING 仍拒绝重复重试。
- Alembic 迁移接在唯一 head 后，并包含明确降级策略。

本模块先故意运行测试，最初出现 7 个失败，分别暴露缺少枚举、Repository 方法、Service 清理和迁移文件；实现后相关测试才转为通过。这就是测试先行：先描述系统必须保护的行为，再写实现。

## 14. 当前还没有实现什么

- Vue 停止按钮和 `AbortController`。
- 真实浏览器断线经过 ASGI Server 的端到端测试。
- 跨进程取消另一台 Worker 上的生成任务。
- 进程崩溃、断电时来不及执行 finally 的 PENDING 超时恢复。
- 取消后的流式原位重试接口。

当前已有的重试接口会一次性返回完整结果；流式重试会在前端流式交互阶段单独设计。

## 15. 常见错误

- 把取消捕获成普通模型错误，错误记录为 FAILED。
- 保存状态后不重新抛出 `CancelledError`，模型请求继续运行。
- `try` 放在 `yield started` 后面，首帧断线无法清理。
- 先查询 PENDING 再普通 UPDATE，覆盖并发完成结果。
- 取消时把部分正文写入 Redis，污染模型上下文。
- completed 已提交后又被取消清理覆盖。
- 只修改 Python Enum，忘记更新 PostgreSQL Enum。
- 认为 finally 可以处理进程强制退出；进程消失时 Python 没有机会运行清理代码。

## 16. 面试表达

> 我把取消作为独立的 CANCELLED 终态，而不是模型 FAILED。Service 单独捕获 CancelledError 和 GeneratorExit，先尽力保存部分正文与取消状态，再重新抛出取消信号以终止下游网络流。Repository 使用 `UPDATE ... WHERE status = PENDING RETURNING id` 完成原子状态转换，避免 completed 与断线同时发生时成功结果被覆盖。取消消息不进入 Redis、模型历史或滚动摘要，并允许复用原助手消息 ID 重新生成。

## 17. 小练习

请解释下面为什么不能分成两条数据库操作：

```text
先 SELECT 确认消息是 PENDING
再 UPDATE 为 CANCELLED
```

提示：思考两条 SQL 中间，模型成功流程是否可能把同一消息改成 `COMPLETED`。

答案要点：两条语句之间存在并发窗口；条件必须放进同一条 UPDATE，由 PostgreSQL 原子决定是否允许状态转换。
