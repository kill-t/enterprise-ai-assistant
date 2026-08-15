# 75. LLM 调用审计与独立事务

## 这一模块解决什么问题

项目此前已经能记录日志和消息 Token，但日志会轮转，消息字段也只能描述最终助手消息，无法统一回答：

- 普通聊天、流式聊天、会话摘要和证据选择分别调用了多少次模型？
- 调用使用了哪个 Provider 和实际模型？
- 成功、失败和取消分别发生了多少次？
- 每次调用耗时多久，消耗了多少 Token？
- 一次模型调用属于哪个 HTTP 请求、用户、会话和助手消息？

本模块新增 `llm_runs`，把这些非敏感元数据保存到 PostgreSQL。

```text
业务 Service
→ AuditedChatProvider
→ DeepSeek / Mock / OpenAI-compatible Provider
→ 成功、失败、取消或流式终态
→ DatabaseLLMRunRecorder
→ 独立 PostgreSQL 事务
→ llm_runs
```

## 为什么不能只看 messages

一条助手消息不一定只对应一次模型调用。

例如 EvidenceSelector 第一次返回非法 JSON，系统会进行一次受控纠错重试：

```text
一条用户问题
→ 第一次 Selector 调用：厂商成功返回，但 JSON 协议不合法
→ 第二次 Selector 调用：纠错后成功
→ 一条最终助手消息
```

如果只在 `messages` 保存 Token，两次真实调用会被压成一次，费用、延迟和故障率都不准确。`llm_runs` 按“一次 Provider 调用”记录，因此每次重试都有独立行。

## 表中保存什么

`llm_runs` 包含：

| 字段 | 含义 |
| --- | --- |
| `request_id` | 与当前结构化日志关联；Celery 等无 HTTP 场景可为空 |
| `user_id` | 当前用户，可为空 |
| `conversation_id` | 关联会话，可为空 |
| `message_id` | 关联助手消息，可为空 |
| `operation` | `conversation_chat`、`grounded_chat`、`conversation_summary`、`evidence_selection` 等用途 |
| `provider`、`model` | 厂商和实际模型 |
| `status` | `SUCCEEDED`、`FAILED`、`CANCELLED` |
| 三种 Token | 输入、输出和总 Token |
| `latency_ms` | Provider 调用耗时 |
| `finish_reason` | 正常完成原因 |
| `error_code` | 稳定错误码 |
| `error_type` | 只保存异常类名，例如 `TimeoutError` |
| 三个时间 | 开始、结束和记录创建时间 |

表上还建立了：

- Token 和耗时非负约束。
- operation、provider、model 非空约束。
- 用户、会话、消息单列索引。
- operation + started_at、status + started_at 组合索引。

后续统计某类调用的成功率和延迟时，不必扫描无关记录。

## 明确不保存什么

审计表没有以下列：

```text
prompt
request
response
content
error_message
```

原因是企业问题、邮件内容、知识库 Chunk、系统 Prompt、模型回答和第三方异常正文都可能包含敏感数据或密钥。

`ChatRequest.metadata` 也不会整包写入数据库。装饰器只读取固定的三个 UUID：

```text
user_id
conversation_id
message_id
```

`operation` 只接受 100 字符以内的小写机器标识；自由文本会变成 `unspecified`。

## 为什么使用装饰器

错误做法是在每个业务 Service 中复制审计逻辑：

```text
ConversationService 记录一次
SummaryProcessor 再写一套
EvidenceSelector 再写一套
未来 Agent Node 再写一套
```

这样容易遗漏失败分支，流式状态也会逐渐不一致。

当前方案把原 Provider 包在 `AuditedChatProvider` 中：

```text
调用方只认识 BaseChatProvider
            ↓
AuditedChatProvider 仍然实现 BaseChatProvider
            ↓
内部转发给真实 Provider
```

这就是装饰器模式：不修改原对象的核心职责，在外层增加可复用能力。业务 Service 不需要知道 SQLAlchemy 审计实体。

## 为什么不直接修改 BaseChatProvider

把数据库代码写进抽象基类会产生两个问题：

- 基础设施协议被 PostgreSQL 绑定，纯内存测试和离线评测也被迫连接数据库。
- Provider 既负责厂商协议，又负责业务持久化，职责混在一起。

现在离线评测仍可注入原始 Provider；FastAPI 在线请求和摘要 Worker 才由工厂组装审计装饰器。

## 普通调用如何记录

非流式调用流程：

```text
记录 started_at 和单调时钟
→ await provider.chat(request)
├─ 返回 ChatResponse
│  → 保存实际模型、Token、finish_reason、SUCCEEDED
├─ 抛出普通异常
│  → 保存稳定错误码、异常类名、FAILED
└─ 任务取消
   → 保存 CANCELLED 后继续抛出取消
```

异常仍会继续交给 ConversationService 或 EvidenceSelector 转换，审计层不会把失败吞掉。

## 流式调用为什么更复杂

流式 Provider 会连续产生事件：

```text
delta → delta → completed
```

也可能：

```text
delta → error
```

或用户收到一个 delta 后关闭页面，后面根本没有终态。

因此装饰器遵守以下规则：

- `delta` 只转发，不写多条数据库记录。
- `completed` 记录一次 `SUCCEEDED` 和最终 usage。
- `error` 记录一次 `FAILED`。
- 流正常结束却没有终态，记录 `LLM_STREAM_PROTOCOL_ERROR`。
- 消费者在中途关闭异步生成器，记录 `CANCELLED`。
- 记录终态后再把终态事件交给上层，避免上层提前关闭导致漏记。

最终一条流只产生一条审计记录，不会按每个文字片段写数据库。

## 审计事务为什么必须独立

聊天业务事务负责：

```text
用户消息 + 助手占位
助手终态 + 引用快照
```

审计事务负责：

```text
一条 llm_runs
```

如果两者共用同一个 `AsyncSession`，审计插入失败可能把已经生成成功的助手消息一起回滚；在等待流式模型时并发使用同一个 Session 也不安全。

`DatabaseLLMRunRecorder` 每次都会：

```text
SessionLocal 创建独立 Session
→ Repository add + flush
→ Recorder commit
→ 关闭 Session
```

若审计写入失败，`AuditedChatProvider` 只记录中文警告，仍返回已经成功的模型结果。真实 smoke 使用不兼容的 Windows 事件循环时，审计写入失败但 Mock 流仍返回了 `delta,error`，证明该降级边界确实生效。

这里的“可降级”只适用于可观测性数据，不表示消息、引用或审批这类业务事实也可以随意丢失。

## 外键删除策略

`user_id`、`conversation_id`、`message_id` 使用 `ON DELETE SET NULL`。

真实 HTTP 验收得到：

```text
创建会话并发送消息
→ llm_runs 正确关联用户、会话和助手消息
→ 删除临时会话
→ 审计行仍保留
→ conversation_id、message_id 自动置空
→ user_id 仍保留
```

这让运维统计不会因为用户删除一次临时会话而突然减少，同时不保留已删除消息的正文，因为审计表本来就没有正文字段。未来若实现账号彻底删除策略，还需要结合隐私要求决定审计记录保留期限。

## Provider 成功不等于业务输出有效

`SUCCEEDED` 的准确含义是：

> Provider 协议正常结束，并返回了可解析为 `ChatResponse` 的结果。

它不表示 EvidenceSelector 的 JSON 一定通过业务 Pydantic 校验，也不表示最终回答一定符合评测标准。

例如厂商正常返回了缺字段 JSON：

```text
llm_runs：SUCCEEDED
EvidenceSelector：LLMEvidenceOutputError
下一次纠错调用：另一条 llm_runs
```

传输可靠性、业务协议正确性和回答质量是三种不同指标，不能混成一个 `success`。

## 迁移与验证

迁移版本：

```text
f4c8a2d6e190
→ a1c5e9d3f702
```

现有开发库执行迁移前后：

```text
users：6 → 6
conversations：5 → 5
messages：16 → 16
```

另外创建了一个名称固定且事先检查不存在的临时数据库，从第一条迁移完整升级到最新 head，得到：

```text
a1c5e9d3f702 (head)
llm_runs 存在
alembic_version 只有 1 条当前版本
```

验证后临时数据库已删除。

### 常见连接串错误

第一次空库测试使用：

```text
postgresql://...
```

SQLAlchemy 因此寻找未安装的 `psycopg2`。项目实际使用 psycopg 3，正确格式是：

```text
postgresql+psycopg://...
```

驱动名称是连接 URL 的一部分，不只是安装依赖就能自动猜对。

### Windows 事件循环错误

第一次独立异步 smoke 使用 Windows 默认 Proactor 循环，psycopg 异步驱动拒绝连接。改用项目已有的 `run_async` 后成功：

```text
Windows → asyncio.SelectorEventLoop
其他平台 → asyncio.run
```

正式本地服务器 `python -m app.local_server` 已使用同一规则。

### 外键实体导入错误

第二次 smoke 出现 `NoReferencedTableError`。数据库中虽然已有外键目标表，但独立脚本只导入了 `LLMRunEntity`，SQLAlchemy 当前进程的 `Base.metadata` 还不知道 `users`、`conversations`、`messages`。

修复后审计实体显式注册三个外键目标，并增加测试直接解析每个 `ForeignKey.column`。这保证 FastAPI、Celery Worker 和独立脚本的导入顺序不同也能工作。

## 测试保护什么

- 表字段、约束和敏感列黑名单。
- 外键在独立导入场景也能解析。
- Repository 只 flush、不提交。
- Recorder 使用自己的 Session 并提交。
- 普通成功记录实际模型、Token、耗时和关联 ID。
- 普通异常只记录错误类名，不保存异常正文，并继续抛出。
- 审计数据库失败不覆盖成功模型结果。
- 流式成功、错误和中途关闭各只写一条终态。
- 普通聊天和 Grounded Chat 注入用户、会话和消息关联。
- 摘要注入会话关联。
- 在线 EvidenceSelector 使用审计 Provider。
- 迁移接在当前 head 后并可降级。

最终结果：

```text
Ruff：通过
pytest：430 passed
现有库迁移：通过
空库完整迁移：通过
真实 HTTP Mock 审计：通过
```

## 关键代码入口

- `backend/app/modules/llm_audit/entity/llm_run.py`：ORM 字段、约束、索引和外键。
- `backend/app/modules/llm_audit/models.py`：不含正文的审计数据契约。
- `backend/app/modules/llm_audit/audited_provider.py`：普通与流式 Provider 装饰器。
- `backend/app/modules/llm_audit/recorder.py`：独立 Session 和事务。
- `backend/app/modules/llm_audit/factory.py`：在线审计 Provider 组装。
- `backend/app/modules/conversation/service/conversation_service.py`：聊天关联 ID 注入。
- `backend/app/modules/conversation/service/conversation_summary_processor.py`：摘要 operation 和会话关联。
- `backend/app/modules/knowledge/retrieval/evidence_selector_factory.py`：在线 Flash Selector 审计接入。
- `backend/migrations/versions/a1c5e9d3f702_create_llm_runs_table.py`：数据库迁移。
- `backend/tests/test_llm_run_audit.py`：审计核心行为测试。

## 当前没有做什么

- 没有开放审计查询 API，普通用户不能读取其他人的调用信息。
- 没有前端用量或费用页面。
- 没有把价格写死在数据库；价格变化后应使用带版本的计价规则计算。
- 没有保存 Prompt 和回答正文。
- 没有为审计写入增加内存后台任务，因为进程崩溃后无法恢复。

后续可在可观测性阶段增加管理员统计、Prometheus 指标和数据保留策略，不需要现在扩大本模块。

## 面试时可以怎么讲

> 我没有把 LLM 用量只塞进最终消息，因为一次业务请求可能包含证据选择、纠错重试、摘要和最终生成等多次模型调用。我在 BaseChatProvider 外增加审计装饰器，统一处理普通成功、异常、流式终态和消费取消。每次调用只保存 Provider、模型、用途、Token、耗时、状态和安全关联 ID，不保存 Prompt、回答或异常正文。审计使用独立 PostgreSQL Session，写入失败会降级日志但不会回滚聊天业务。真实迁移和 HTTP smoke 还验证了会话删除后审计保留、资源外键自动置空。

## 15–30 分钟小练习

假设一次 RAG 聊天发生：

```text
EvidenceSelector 第一次返回非法 JSON
→ 纠错重试成功
→ 最终聊天模型流式成功
```

请回答：

1. 应该产生几条 `llm_runs`？
2. 第一条 Selector 的 Provider 状态为什么可能仍是 `SUCCEEDED`？
3. 如果审计数据库临时不可用，最终聊天是否应该失败？为什么？
4. 为什么不能把完整 `ChatRequest.metadata` 直接保存为 JSON？

答案要点：共三条；Provider 正常返回不等于业务 JSON 校验通过；审计属于可降级可观测性，不能覆盖成功业务；metadata 将来可能混入自由文本或敏感字段，只能白名单提取。
