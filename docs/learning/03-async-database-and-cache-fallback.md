# 模块 03：异步数据库与缓存降级

## 要解决的问题

消息 Router 已经是 `async`，但原来的数据库代码仍使用同步 `Session`。同步查询、
提交和刷新会阻塞 FastAPI 的事件循环；同时，每个 Repository 自己提交事务，导致
Service 无法表达完整的业务边界。

另一个问题是 Redis 与 PostgreSQL 的重要性没有区分：Redis 故障会让用户看到失败，
即使用户消息已经成功写入 PostgreSQL。

## 数据流

```text
用户发送消息
    ↓
AsyncSession 写入用户消息并提交
    ↓
同步 Provider 在线程中调用
    ↓
AsyncSession 写入助手消息并提交
    ↓
尽力写入 Redis 短期记忆
    ↓
返回助手消息
```

## 设计决策

### 使用 AsyncSession

`create_async_engine` 配合 `postgresql+psycopg://` 创建异步引擎；Repository 的
查询、`flush` 和 `refresh` 都必须 `await`。

Alembic 仍保留同步数据库 URL。迁移是管理命令，不在 FastAPI 请求事件循环中运行，
不需要为了运行时异步化而改变。

### Repository 不提交事务

Repository 只负责：

- `add` 实体。
- `flush` 让数据库生成主键等字段。
- `refresh` 获取数据库回写的数据。
- 查询实体。

Service 才负责 `commit` 和 `rollback`。这样业务层能明确决定何时把一组数据库变化
作为正式结果保存。

### 两个短事务

不能在调用模型期间保持数据库事务：模型是外部网络调用，可能慢或失败，长期占用
数据库连接会降低并发能力。

因此先提交用户消息，再调用模型；模型成功后再提交助手消息。模型失败时保留用户消息，
这是可追踪的真实业务状态，不是脏数据。

### PostgreSQL 与 Redis 的关系

- PostgreSQL 是正式记录和唯一事实来源。
- Redis 是最近对话的短期缓存。
- Redis 读取失败或缓存为空时，从 PostgreSQL 读取历史。
- Redis 写入失败时记录告警，聊天仍可成功返回。

这不等于忽略 Redis 故障，而是因为数据库中的正式消息仍可用于恢复缓存。

## 关键调用链

```text
FastAPI Router
    ↓
get_db() 产生 AsyncSession
    ↓
ConversationService
    ↓
ConversationRepository / MessageRepository
    ↓
PostgreSQL

ConversationService
    ↓
MemoryManager
    ↓
RedisMemory（可降级）
```

## 测试说明

- 验证 PostgreSQL URL 被转换成 psycopg 异步方言。
- 验证所有数据库驱动的会话 Router 都是异步函数。
- 验证 Redis 读取失败时，历史从数据库恢复。
- 验证 Redis 写入失败时，不向上抛出缓存异常。

本模块测试使用 FakeMemory、AsyncMock 和内存中的 ORM 实体，因此不依赖真实 Redis、
PostgreSQL 或模型服务。后续会增加 Testcontainers 集成测试来验证真实基础设施。

## 面试表达

“我把 FastAPI 的数据库访问从同步 Session 迁移为 AsyncSession，并将事务提交权收回
Service 层。对话消息以 PostgreSQL 为事实来源，Redis 只承担短期缓存；缓存失败时可
从数据库回填，避免缓存故障影响已成功持久化的用户对话。”

## 小练习

现在用户消息会先被提交。如果模型调用超时，应该如何让前端和数据库都明确知道这次
请求失败了？请设计一个消息状态或错误记录方案，不必写代码。
