# 模块 02：Redis 短期记忆接入

## 要解决的问题

Memory 类已经存在，但消息接口无法正确使用它：

- `ConversationService` 构造函数需要 `MemoryManager`，依赖工厂却只传入了数据库 Session。
- `ConversationService.send_message()` 已经是异步函数，Router 仍是普通函数，会把 coroutine 当作响应返回。
- Redis 客户端没有明确的关闭时机。

## 调用链

```text
POST /api/v1/conversations/{conversation_id}/messages
    ↓
async send_message Router
    ↓
ConversationService.send_message
    ↓
MemoryManager
    ↓
RedisMemory
    ↓
Redis List: memory:conversation:{conversation_id}
```

## 设计与职责

- Router：接收 HTTP 请求，并 `await` 异步 Service。
- `get_memory_manager`：FastAPI 依赖，创建 Redis 客户端、组装 `MemoryManager`，并在请求结束后关闭客户端。
- `MemoryManager`：对业务层提供 `save_message` 和 `get_context`，不泄漏 Redis 细节。
- `RedisMemory`：将消息序列化到 Redis List，截断到最近 20 条，并设置 24 小时过期时间。
- `ConversationService`：保存用户和助手消息后，将两者写入短期记忆；它不直接导入或调用 Redis。

## 为什么使用依赖注入

如果在 `ConversationService` 内部直接创建 Redis 客户端，会出现三个问题：

1. Service 同时负责业务编排和基础设施创建，职责混乱。
2. 单元测试无法轻易替换成假内存实现。
3. 客户端资源的关闭位置不清楚，容易泄漏连接。

FastAPI Dependency 把“如何创建和释放资源”放在外围，把“如何使用记忆”留给业务层。

## 测试说明

- `test_memory_manager_round_trips_messages`：验证 `MemoryManager` 能将读写委托给底层 Memory 实现。
- `test_conversation_service_dependency_injects_memory_manager`：防止未来漏传 `MemoryManager`。
- `test_send_message_router_is_async_and_awaits_service`：防止异步 Service 被 Router 直接返回而没有等待。

根目录的 `pytest.ini` 规定了测试目录和 Python 导入路径；`config.py` 将 `.env` 定位到
`backend/.env`，因此从项目根运行测试时也能加载本地配置。

## 面试表达

“我把短期记忆设计为业务层依赖的抽象接口。FastAPI 的生成器依赖负责 Redis 客户端的
生命周期，Service 只依赖 MemoryManager。这样既便于后续将 Redis 替换为其他实现，也能
在单元测试中用 FakeMemory 验证业务行为。”

## 小练习

请思考：如果 Redis 在 `save_message` 时暂时不可用，目前的聊天请求会发生什么？
在下一阶段，我们应该选择“让整个聊天失败”还是“数据库消息已经成功保存时继续返回回复”？
请说明你的选择和原因。
