# 会话摘要表与覆盖游标

## 1. 本模块做了什么

本模块只建立滚动摘要的持久化基础，还没有调用模型生成摘要，也没有把摘要放进聊天 Prompt。

它回答三个问题：

```text
摘要保存在哪里？
摘要已经覆盖到哪条消息？
下一次更新摘要时从哪里继续？
```

新增内容：

- `conversation_summaries` 数据库表。
- `ConversationSummaryEntity` ORM 实体。
- `ConversationSummaryContent` 固定摘要结构。
- `ConversationSummaryRepository` 异步数据库访问。
- Alembic 迁移和相应测试。

## 2. 原始消息、摘要和 Redis 的区别

```text
PostgreSQL messages
→ 保存全部原始消息，是事实来源

PostgreSQL conversation_summaries
→ 保存较早历史的压缩副本

Redis
→ 临时缓存最近消息，丢失后可以从 PostgreSQL 恢复
```

创建摘要不会删除 `messages` 中的任何消息。摘要内容错误时，仍然可以根据原始消息重新生成。

## 3. 表结构

`conversation_summaries` 主要字段：

| 字段 | 作用 |
| --- | --- |
| `conversation_id` | 所属会话，同时作为主键 |
| `content` | 可直接放进 Prompt 的固定 Markdown 摘要 |
| `covered_until_created_at` | 摘要覆盖到的消息时间 |
| `covered_until_message_id` | 摘要覆盖到的精确消息 ID |
| `token_count` | 摘要本身的估算 token 数 |
| `version` | 摘要更新版本，从 1 开始 |
| `created_at` | 首次创建时间 |
| `updated_at` | 最近更新时间 |

为什么没有单独的 `summary_id`？

因为第一版规定一个会话只有一条滚动摘要。直接让 `conversation_id` 成为主键，数据库会阻止同一个会话插入第二条摘要。

## 4. 什么是覆盖游标

假设会话有 100 条消息，第一次摘要处理了第 1～60 条：

```text
摘要正文：第 1～60 条消息的压缩结果
covered_until_message_id：第 60 条消息 ID
covered_until_created_at：第 60 条消息时间
version：1
```

下一次更新时，只需要读取：

```text
第 60 条之后的旧历史
```

假设第二次又处理到第 80 条：

```text
摘要正文：旧摘要 + 第 61～80 条的新信息
covered_until_message_id：第 80 条消息 ID
version：2
```

这两个游标就像书签，能够防止：

- 重复总结第 1～60 条。
- 跳过尚未总结的消息。
- 摘要正文更新失败但游标已经错误前进。

## 5. 为什么摘要正文和游标必须一起提交

以后摘要 Service 应在一个 PostgreSQL 事务中执行：

```text
更新 content
→ 更新 covered_until_*
→ version + 1
→ commit
```

如果任意一步失败，全部回滚。

不能先提交游标、再保存正文。否则系统可能认为某些消息已经总结，但实际摘要中没有它们，后续也不会再次处理，最终造成历史信息遗漏。

Repository 只执行 `add / flush / refresh`，不调用 `commit()`。事务边界以后由摘要 Service 控制。

## 6. 固定摘要结构

`ConversationSummaryContent` 要求五个栏目：

```text
用户目标与背景
已确认的事实、约束和决定
已完成事项
未解决问题与待办
对后续回答有价值的偏好
```

Pydantic 会拒绝空栏目，然后 `to_prompt_text()` 按固定顺序生成 Markdown。

使用结构化模型而不是让模型自由输出，可以：

- 保持多次摘要更新格式一致。
- 更容易测试遗漏栏目。
- 以后改成 LLM 结构化输出时直接复用 Schema。
- 面试演示时清楚展示摘要内容。

## 7. 外键和约束

### 会话删除

```text
conversation_summaries.conversation_id
→ conversations.id
→ ON DELETE CASCADE
```

删除会话时，数据库自动删除摘要，不留下孤立数据。

### 游标消息删除

```text
covered_until_message_id
→ messages.id
→ ON DELETE SET NULL
```

如果某条游标消息被单独删除，消息 ID 会置空，但时间游标仍然保留，方便诊断。正常业务暂时不会单独删除已完成历史消息。

### 检查约束

```text
token_count >= 0
version >= 1
```

这些是数据库最后一道数据完整性保护，不依赖应用代码是否正确。

## 8. 为什么移除了双向 ORM Relationship

第一次实现时，摘要和会话增加了双向 SQLAlchemy relationship。定向测试只导入摘要实体时，SQLAlchemy 找不到尚未注册的 `ConversationEntity`，导致 Mapper 初始化失败。

本模块并不需要通过 `conversation.summary` 隐式加载摘要：

- 摘要有独立 Repository。
- 删除级联由数据库外键完成。
- 显式查询更容易观察 SQL 和控制事务。

因此移除了非必要 relationship，降低实体模块之间的导入耦合。不是所有外键都必须对应 ORM relationship。

## 9. 迁移操作

查看当前迁移版本：

```powershell
cd backend
.\.venv\Scripts\alembic.exe current
```

执行到最新版本：

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

查看最新 head：

```powershell
.\.venv\Scripts\alembic.exe heads
```

本模块迁移：

```text
a4d8f2c7e615
→ d91e7f3a2c40
```

## 10. 测试保护什么

- 会话 ID 是唯一主键。
- 删除会话时摘要使用数据库级联。
- 游标消息删除时 ID 可以置空。
- 五个摘要栏目顺序固定且不能为空。
- Repository 创建、查询和更新都不提交事务。
- 迁移接在旧 head 后，并包含所有必要约束。
- 从空库到最新 head 可以生成完整迁移 SQL。

这些测试目前主要是单元测试、元数据测试和迁移协议测试。真实 PostgreSQL 集成测试会在引入 Testcontainers 后补充。

## 11. 面试表达

可以这样说明：

> 原始消息始终完整保存在 PostgreSQL，摘要只是旧历史的压缩副本。我为每个会话设计了一条滚动摘要，并用消息 ID 和创建时间作为覆盖游标。摘要正文、游标和版本必须在同一个事务里更新，避免游标前进但摘要内容遗漏。conversation_id 直接作为主键，保证一会话一摘要；会话删除时由数据库级联清理摘要。

## 12. 小练习

当前摘要覆盖到消息 40，版本为 2。后台任务准备把消息 41～60 合入摘要，但在更新正文后、提交事务前进程崩溃。

请回答：

1. 如果正文和游标在同一个事务中，重启后版本和游标是什么？
2. 为什么下一次任务仍然能够重新处理消息 41～60？
3. 如果游标提前单独提交到消息 60，会发生什么数据问题？

答案要点：事务未提交时全部回滚，摘要仍是版本 2、覆盖到消息 40；下一次仍从消息 40 后读取；如果游标提前提交，消息 41～60 可能永久遗漏。
