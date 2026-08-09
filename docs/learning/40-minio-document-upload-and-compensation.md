# MinIO 文档上传、去重与失败补偿

## 1. 这一小步完成了什么

上一模块创建的知识库还是一个“空文件夹”。本模块让用户可以上传 PDF、TXT 和 Markdown
原文件，并查询文档当前状态。

新增接口：

```text
POST /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET  /api/v1/knowledge-bases/{knowledge_base_id}/documents
GET  /api/v1/documents/{document_id}
```

POST 使用 `multipart/form-data`，成功返回 `202 Accepted`，不是 `200`。`202` 表示服务器
已经可靠接收文件并创建后台任务，但解析、分块和 Embedding 尚未完成。

当前文档会停在：

```text
Document.status = PENDING
IngestionJob.status = PENDING
```

下一模块的持久化 Worker 才会继续处理它。

## 2. 为什么原文件进入 MinIO，而不是 PostgreSQL

PostgreSQL 擅长结构化业务数据、事务、过滤和关联；PDF 等二进制大文件会让数据库备份、
查询和连接压力变大。MinIO 是兼容 S3 协议的对象存储，更适合保存原文件。

本项目的职责分工：

```text
MinIO
  └─ 原始 PDF / TXT / Markdown 字节

PostgreSQL
  ├─ 文件名、大小、SHA-256、Object Key
  ├─ 文档状态与错误
  └─ 持久化入库任务
```

数据库中的 `object_key` 类似“对象存储里的地址”，但不是公开下载 URL。存储桶默认保持
私有，后续下载应经过身份认证或生成短期签名 URL。

## 3. 完整上传数据流

```text
客户端 multipart 文件
  ↓
Router 取得 JWT 当前用户和 UploadFile
  ↓
Service 查询 knowledge_base_id + user_id
  ↓
拒绝已归档知识库
  ↓
校验文件名、扩展名、MIME 和 20 MB 上限
  ↓
分块读取并计算真实大小与 SHA-256
  ↓
查询同知识库是否已有相同 SHA-256
  ↓
MinIO 保存原文件
  ↓
PostgreSQL 同一事务创建 Document + IngestionJob
  ↓
commit → 返回 202
```

所有权在读取文件前校验。这样越权请求不会让服务器白白读取一个 20 MB 文件，也不会通过
错误差异判断别人的知识库是否存在。

## 4. 为什么要分块计算 SHA-256

错误做法是：

```python
content = await file.read()
```

这会把整个文件一次放进 Python 内存。10 个用户同时上传 20 MB，就可能额外占用约
200 MB。

当前实现每次读取 1 MB：

```text
读取一块
→ size 累加
→ 超过上限立即失败
→ SHA-256 digest 更新
→ 继续下一块
```

FastAPI 的 `UploadFile` 底层本身使用可落盘的临时文件。算完哈希后执行 `seek(0)`，MinIO
SDK 再从开头读取同一个流，因此没有复制第二份完整文件。

请求头中的文件大小不能作为唯一依据，因为客户端可以伪造；系统按实际读到的字节数判断。

## 5. SHA-256 去重为什么仍要数据库唯一约束

同一知识库中的重复内容会返回：

```text
HTTP 409
DOCUMENT_ALREADY_EXISTS
```

Service 先查 SHA-256，可以在上传 MinIO 前快速拒绝常见重复。但两个并发请求仍可能同时
查到“不存在”。数据库的唯一约束
`UNIQUE(knowledge_base_id, sha256)` 是最终保护。

若第二个请求已经上传对象后才撞上唯一约束，系统会：

```text
rollback 数据库事务
→ 删除第二个请求刚上传的对象
→ 返回 409
```

## 6. PostgreSQL 和 MinIO 为什么不能一起回滚

数据库事务只能控制 PostgreSQL，无法控制另一个网络服务。

下面这种情况必须处理：

```text
MinIO put_object 成功
→ PostgreSQL INSERT 或 commit 失败
```

如果什么都不做，MinIO 会留下数据库不知道的“孤立对象”。本模块使用补偿事务：

```text
try:
    上传 MinIO
    写入数据库
    commit
except 数据库异常:
    rollback
    删除刚上传的 MinIO 对象
    继续抛出原异常
```

补偿不是数据库 ACID 事务。删除对象本身也可能失败，所以代码会记录包含 `object_key` 的
中文异常日志，同时保留最初的数据库错误。后续生产化可以增加孤立对象扫描器或持久化清理
任务，做到最终一致。

## 7. 为什么 MinIO 适配器要使用 `asyncio.to_thread`

FastAPI 路由和 Service 使用异步 I/O，但官方 MinIO Python SDK 的常用接口是同步函数。
如果直接在事件循环中调用，同一进程的其他请求会等待上传完成。

适配器这样转换边界：

```text
Service await BaseObjectStorage.put_object(...)
  ↓
MinioObjectStorage
  ↓ asyncio.to_thread
同步 SDK put_object(...)
```

业务层只认识 `BaseObjectStorage`。单元测试可以替换成 AsyncMock，以后换成其他 S3 兼容
服务时也不需要修改文档 Service。

## 8. 文件安全边界

### 不相信客户端路径

浏览器可能提交：

```text
C:\fakepath\manual.pdf
../../manual.pdf
```

系统只保留最后的 `manual.pdf` 作为展示名。真正的 MinIO Object Key 完全由服务端 UUID
构造：

```text
users/{user_id}/knowledge-bases/{knowledge_base_id}/documents/{document_id}.pdf
```

因此用户文件名不能改变存储目录，也不会互相覆盖。

### MIME 不是最终内容证明

扩展名和 MIME 校验只能提前挡住明显错误。客户端仍可能把其他内容伪装成 PDF。下一模块
Parser 必须把文件内容视为不可信输入，并让无法解析、加密或无文本 PDF 明确进入 FAILED。

### 当前限制

- PDF、TXT、`.md`、`.markdown`。
- 单文件默认最大 20 MB。
- 不支持 Word、Excel、PPT 和图片。
- 不做 OCR。
- 不提供公开 Bucket 或永久公开链接。

## 9. 关键代码入口

```text
app/modules/knowledge/api/document_router.py
  HTTP、multipart、202 状态码

app/modules/knowledge/service/document_service.py
  所有权、校验、哈希、去重、事务和补偿

app/modules/knowledge/repository/document_repository.py
  Document + Job 原子写入与所有权 SQL

app/infrastructure/object_storage/base.py
  业务层依赖的抽象接口

app/infrastructure/object_storage/minio_storage.py
  MinIO SDK 与异步线程池适配
```

## 10. 本地启动和查看 MinIO

仓库根目录执行：

```powershell
docker compose -f infra/docker/docker-compose.yml up -d minio
```

MinIO API：`http://127.0.0.1:9000`

MinIO 控制台：`http://127.0.0.1:9001`

本地默认账号和密码都是 `minioadmin`，只允许用于本机开发。正式环境必须通过环境变量替换，
并且不能把真实密钥提交到 Git。Compose 端口只绑定 `127.0.0.1`，不会默认监听局域网
网卡；这个沙箱镜像不应直接暴露到公网。

停止但保留 Docker Desktop 容器和数据卷：

```powershell
docker compose -f infra/docker/docker-compose.yml stop minio
```

不要使用 `down -v`，它会删除包括 `minio_data` 在内的命名卷。

## 11. 测试保护了什么

### 单元测试

- 空文件、超过上限、扩展名和 MIME 错误。
- 归档知识库在读取文件前失败。
- 常规重复上传在写 MinIO 前失败。
- MinIO 失败时不创建数据库记录。
- 数据库失败时回滚并补偿删除对象。
- 并发重复触发唯一约束后仍清理对象并返回 409。
- 补偿删除失败不覆盖原数据库异常。
- 文档列表和详情包含用户所有权过滤。
- MinIO 适配器只建桶一次，并正确传递上传参数。
- multipart API 返回 202 且受 Bearer Token 保护。

### 真实基础设施验证

本次使用真实 PostgreSQL 与 MinIO 执行：

```text
临时知识库
→ 上传 TXT
→ MinIO stat_object 确认对象大小
→ 确认一条 PENDING Job
→ 重复上传得到 409
→ 列表和详情读取成功
→ 回滚临时数据库记录
→ 删除临时 MinIO 对象
```

最终再次确认临时知识库、文档和对象都不存在。

## 12. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| `OBJECT_STORAGE_UNAVAILABLE` | MinIO 未启动、端口或账号不一致；先检查 Docker Desktop 和 `.env`。 |
| 上传后文档一直 `pending` | 当前 Worker 尚未实现，这是本模块的预期状态。 |
| 相同文件返回 409 | 同一知识库按文件内容 SHA-256 去重，不按文件名去重。 |
| MinIO 有对象但数据库没有记录 | 补偿删除也失败；根据日志中的 Object Key 清理，后续增加扫描器。 |
| 直接调用同步 MinIO SDK 卡住其他请求 | 必须通过异步适配器在线程池执行。 |
| Docker Desktop 看不到 MinIO | 尚未执行一次 `compose up -d minio`；执行后用 `stop` 保留容器。 |

## 13. 面试表达

> 原文件进入私有 MinIO，PostgreSQL 保存文件元数据、状态和持久化任务。上传过程分块计算
> 真实大小和 SHA-256，同知识库先预检查去重，再依靠数据库唯一约束处理并发。由于对象
> 存储和 PostgreSQL 不能共享本地事务，我在 MinIO 成功、数据库失败时执行补偿删除，并
> 保留原始错误。官方 MinIO Python SDK 是同步的，所以通过统一异步适配器和线程池隔离，
> Service 不依赖具体厂商 SDK。

## 14. 小练习

请用 15–30 分钟增加一个测试：上传文件名 `C:\\fakepath\\meeting.txt`，断言传给
Repository 的 `filename` 最终是 `meeting.txt`，对象键中也不能出现 `fakepath`。

这次只要求改测试，不需要改主体代码。练习重点：

- 找到 `create_with_job` 的调用参数。
- 理解“展示文件名”和“对象键”是两个不同概念。
- 证明客户端路径不能控制服务端存储位置。

答案要点：读取 `service.repository.create_with_job.await_args.kwargs`，检查 `filename`；再从
`storage.put_object.await_args.kwargs["object_key"]` 检查对象键。

## 15. 2026 技术更新与参考资料

MinIO Server 的官方 GitHub 仓库已在 2026 年归档，因此本项目把固定版本 MinIO 限定为
本地、不可公网访问的可复现办公沙箱。生产部署应重新评估仍在维护的 S3 兼容服务。由于
Service 只依赖 `BaseObjectStorage`，替换存储实现不会改变上传业务规则。

- [MinIO Python SDK 官方仓库](https://github.com/minio/minio-py)
- [MinIO Server 官方发布记录](https://github.com/minio/minio/releases)
- [MinIO 官方 Docker 镜像标签](https://hub.docker.com/r/minio/minio/tags)
