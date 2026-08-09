# 文档入库 Worker、原子索引与失败重试

## 1. 这一小步解决什么问题

文档上传模块已经做了两件事：

```text
MinIO 保存原文件
PostgreSQL 创建 PENDING 文档和 PENDING 入库任务
```

Parser、Chunker 和 Embedding 模块也已经分别完成，但之前还没有代码把它们连起来。本模块
新增持久化文档入库 Worker：

```text
领取任务
→ 下载并校验原文件
→ 解析
→ 分块
→ 生成向量
→ 原子替换 Chunk
→ 文档 READY、任务 SUCCEEDED
```

本模块是 RAG-3A。它建立“任务怎样安全执行”的核心。上传后自动投递、Broker 失败恢复、
RUNNING 进程中断恢复属于紧接着的 RAG-3B。

## 2. 为什么不能在上传接口中直接处理

PDF 解析和 Embedding 可能需要几秒甚至几十秒。如果上传 HTTP 请求一直等待：

- 浏览器容易超时。
- 一个慢模型会长时间占用 API Worker。
- 服务重启后处理进度丢失。
- 用户无法轮询 PENDING、PROCESSING、READY 或 FAILED。

因此上传接口只可靠保存原文件和任务。Celery 消息以后只携带 `job_id`，真正状态始终从
PostgreSQL 读取。即使 Redis Broker 丢失消息，数据库中的任务仍可被恢复扫描发现。

## 3. 三段事务与一次无事务处理

这是本模块最重要的设计：

```text
事务 1
  PENDING 任务 → RUNNING
  attempts + 1
  PENDING 文档 → PROCESSING
  COMMIT

事务 2
  查询文件定位、SHA-256、Embedding Profile
  复制成不可变普通数据
  COMMIT，释放只读事务

无数据库事务
  MinIO 下载
  Parser
  Chunker
  Embedding

事务 3
  删除该文档旧 Chunk
  加入全部新 Chunk
  文档 → READY
  任务 → SUCCEEDED（必须匹配 attempts）
  COMMIT
```

不能从事务 2 一直开着事务等待模型。数据库事务不是“函数作用域越大越安全”，长事务会
占连接、保留旧快照，并增加锁冲突。

最后一次事务故意同时包含 Chunk、文档和任务：

- 全部成功：数据库一次提交完整索引。
- 任一 SQL 或提交失败：全部回滚，旧 Chunk 不会被半删半写。
- 迟到 Worker 的 `attempts` 不匹配：任务成功 UPDATE 不命中，暂存的新 Chunk 和 READY
  状态整体回滚。

## 4. 原子领取怎样阻止重复执行

Celery 的交付语义可能导致同一消息出现多次。不能先 `SELECT` 再普通 `UPDATE`：两个
Worker 可能同时读到 PENDING。

`DocumentIngestionJobRepository.claim_pending()` 使用一条条件 UPDATE：

```text
WHERE id = 当前任务
  AND status = PENDING
  AND attempts < max_attempts
  AND next_retry_at 已到期

SET status = RUNNING,
    attempts = attempts + 1
RETURNING 当前任务
```

数据库只会让一个并发 Worker 更新成功。其他 Worker 得到 `None` 后返回 `skipped`，不会
下载文件，也不会调用 Embedding。

## 5. attempts 为什么像执行版本号

假设第一次 Worker 调用模型很慢，恢复机制已经把任务重新执行到第二次：

```text
Worker A：attempts = 1，迟到返回
Worker B：attempts = 2，正在执行或已经完成
```

Worker A 最后只能执行：

```text
UPDATE ...
WHERE status = RUNNING AND attempts = 1
```

如果数据库已经是 attempts 2，这条 UPDATE 不命中。Worker A 会回滚自己暂存的 Chunk，
不能覆盖 Worker B。这叫乐观并发保护，不需要长时间持有数据库锁。

## 6. 为什么下载后还要校验大小和 SHA-256

PostgreSQL 在上传时保存了：

```text
size_bytes
sha256
object_key
```

Worker 从 MinIO 下载后重新计算大小与 SHA-256。这样能够发现：

- 对象键对应的文件被手工替换。
- 下载内容不完整。
- 数据库记录和对象存储发生不一致。

MinIO 适配器最多读取 `size_bytes + 1` 字节。如果对象比记录更大，会立即拒绝，不会无限
把异常大对象读入内存。无论成功还是失败，都调用 `close()` 和 `release_conn()` 归还 HTTP
连接。

大小或哈希不一致属于不可重试的数据完整性错误。MinIO 临时无法连接则属于可重试错误。

## 7. Processor 为什么分成 prepare、process、persist

入口：
`backend/app/modules/knowledge/service/document_ingestion_processor.py`

### prepare

查询并复制：

- 文件名和 Object Key。
- 文件大小和 SHA-256。
- 知识库冻结的 Embedding Provider、模型和维度。

返回 `DocumentIngestionSource`，它是不可变 dataclass，不依赖 ORM Session。

### process

在数据库事务外完成：

```text
get_object
→ SHA-256 校验
→ ParserRegistry.get_parser
→ TokenAwareChunker
→ EmbeddingProviderFactory.create_service(知识库快照)
→ ProcessedDocumentIngestion
```

### persist

调用 Repository 暂存删除旧 Chunk、加入新 Chunk和文档 READY，但不提交。Worker 随后用
同一事务把任务标记为 SUCCEEDED。

这种拆分也让单元测试可以用假 Processor 专门验证 Worker 状态机，而不用每个测试都真的
解析 PDF 和生成 1024 维向量。

## 8. 重试与永久失败怎样区分

`DocumentIngestionFailurePolicy` 集中分类：

可重试：

- MinIO 临时不可用。
- 处理超时或 Celery 软超时。
- Embedding 网络调用失败。
- Embedding 临时返回无效协议。

不可重试：

- 加密、损坏、扫描型无文字 PDF。
- TXT 编码错误或空文档。
- 原文件大小或 SHA-256 不一致。
- Embedding 配置或维度不一致。
- 任务关联来源不存在。

可重试失败按指数退避：

```text
第 1 次失败：30 秒后
第 2 次失败：60 秒后
第 3 次失败：达到 max_attempts 后永久失败
```

当前模块会可靠保存 `next_retry_at` 和 PENDING 状态。下一模块的恢复扫描器负责在时间到达
后重新投递，不能使用不可恢复的内存定时器。

## 9. 状态怎样同步

成功：

```text
document_ingestion_jobs.status = SUCCEEDED
documents.status = READY
documents.chunk_count = 实际 Chunk 数
```

临时失败：

```text
job = PENDING + next_retry_at
document = PENDING + 安全错误信息
```

永久失败：

```text
job = FAILED
document = FAILED
两者保存相同稳定 error_code / error_message
```

原始第三方异常只进入带关键字段的中文服务端日志。数据库不保存密钥、MinIO 内部响应或
模型返回正文。

## 10. Celery 入口做什么

入口：
`backend/app/modules/knowledge/tasks/document_ingestion_task.py`

稳定任务名是：

```text
knowledge.process_document_ingestion_job
```

同步 Celery Task 使用 `asyncio.run()` 进入异步 Service。每次结束后主动 dispose SQLAlchemy
连接池，避免上一次事件循环创建的异步连接被下一个事件循环复用。

文档任务路由到 `document-ingestion` 队列，避免以后大量 PDF 阻塞短会话摘要任务。

## 11. 关键代码入口

- 状态任务 Repository：
  `backend/app/modules/knowledge/repository/document_ingestion_job_repository.py`
- 文档与 Chunk Repository：
  `backend/app/modules/knowledge/repository/document_index_repository.py`
- 三阶段处理器：
  `backend/app/modules/knowledge/service/document_ingestion_processor.py`
- Worker 编排：
  `backend/app/modules/knowledge/service/document_ingestion_worker_service.py`
- 失败分类：
  `backend/app/modules/knowledge/service/document_ingestion_failure_policy.py`
- MinIO 下载：
  `backend/app/infrastructure/object_storage/minio_storage.py`
- Celery 入口：
  `backend/app/modules/knowledge/tasks/document_ingestion_task.py`

## 12. 测试保护了什么

`tests/test_document_ingestion_worker.py` 覆盖：

- 领取使用单条条件 UPDATE。
- 重复或已结束消息不会处理文件。
- 成功结果等待最后一次原子提交。
- 永久解析失败先回滚半成品，再同步标记文档和任务失败。
- MinIO 临时失败保存延迟重试。
- 迟到 Worker 无法提交旧 attempts 的 Chunk。
- 真实 TXT → Parser → Chunker → Mock Embedding 链路能产生 1024 维向量。
- 下载内容被替换时 SHA-256 校验失败。
- Repository 暂存删旧 Chunk、写新 Chunk 和 READY。
- Celery 能发现稳定任务名。

`tests/test_object_storage.py` 还验证下载大小边界和 HTTP 连接释放。

## 13. 常见错误怎样调试

### 文档一直是 PENDING

如果只查看 RAG-3A 的提交，上传后的任务尚未投递。当前项目已在下一模块完成 RAG-3B，
可以按顺序检查：

```text
数据库 job 是否 PENDING
→ dispatch_claimed_at 是否被领取
→ celery_task_id 是否写回
→ Worker 是否消费 document-ingestion 队列
```

### 文档一直是 PROCESSING

说明 Worker 已取得执行权，但进程可能在处理期间退出。当前恢复扫描会根据 `started_at`
识别超时 RUNNING，并重新排队或永久失败。

### 任务成功但没有 Chunk

正常设计不允许出现这种情况。检查是否绕过 Worker 直接改状态，以及最终 Chunk、READY、
SUCCEEDED 是否还在同一事务中。

## 14. 面试表达

> 我的 RAG 入库不是一个不可恢复的 FastAPI BackgroundTask，而是 PostgreSQL 持久化任务
> 加 Celery 执行。任务通过条件 UPDATE 原子领取，用 attempts 阻止迟到 Worker 覆盖新
> 执行。外部解析和 Embedding 不占数据库长事务，最终以一个事务替换 Chunk、更新文档
> READY 并完成任务。下载内容还会重新校验大小和 SHA-256。临时错误进入指数退避，永久
> 内容错误明确失败。

## 15. 小练习（15–30 分钟）

为对象存储补一个测试：MinIO 返回 `max_bytes + 1` 字节时，应该抛出
`ObjectStorageReadLimitExceededError`，并且 `close()`、`release_conn()` 仍各调用一次。

建议顺序：

1. 先在 `tests/test_object_storage.py` 写失败测试。
2. 使用 `pytest.raises` 捕获指定错误。
3. 断言响应连接已经释放。
4. 运行目标测试；现有实现应直接通过。

答案要点：异常发生在 `try` 内，连接释放写在 `finally`，因此读取超限也不会泄漏连接。
