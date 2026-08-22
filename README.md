# Enterprise AI Assistant

面向 AI 应用后端岗位的企业办公智能助手项目。

项目将逐步实现多轮会话、可追溯知识检索、基于 MCP 的办公工具调用、受控
多 Agent 工作流、人工审批和可观测的任务执行。

## 仓库结构

- `backend/`：FastAPI 应用、数据库迁移和后端测试。
- `frontend/`：Vue 3 演示界面。
- `infra/`：本地基础设施与部署配置。
- `docs/`：架构决策、项目上下文和学习笔记。
- `scripts/`：开发与演示辅助脚本。

项目正在迭代中。每完成一个可运行的功能切片，都会补充对应的启动、验证和
演示说明。

## Windows 本地启动

第一次启动前，确保已安装 Docker Desktop、Python 3.12 和 Node.js。以下命令在
PowerShell 中执行。

### 1. 准备本地配置

首次使用时复制模板；已有 `backend/.env` 时不要覆盖它。

```powershell
Copy-Item backend/.env.example backend/.env
```

在 `backend/.env` 中填入安全随机的 JWT 密钥，至少 32 字节。可用下面命令生成：

```powershell
backend/.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

将输出写入：

```text
AUTH__SECRET_KEY=生成的随机值
```

第一轮演示建议保持：

```text
DEFAULT_PROVIDER=mock
EMBEDDING__PROVIDER=mock
RAG__EVIDENCE_SELECTOR=threshold
```

聊天 Mock 仍会走真实数据库、Redis、SSE 和前端状态链路；Embedding Mock 会生成稳定的
1024 维词项哈希向量。两者都不调用外部模型。词项哈希只适合本地测试，不等于真实语义
检索。真实模型需要在本机 `.env` 填写对应密钥并切换 Provider；任何密钥都不能提交到 Git。

RAG 第二阶段默认使用不产生远程费用的向量阈值。需要启用已经通过困难集评测的
DeepSeek V4 Flash 证据选择时，在本机 `.env` 配置：

```text
DEEPSEEK__API_KEY=你的真实密钥
RAG__EVIDENCE_SELECTOR=llm
RAG__LLM_EVIDENCE_SELECTOR_MODEL=deepseek-v4-flash
RAG__LLM_EVIDENCE_SELECTOR_THINKING_ENABLED=false
```

如果还要让最终聊天回答使用真实 DeepSeek，并为较长的 RAG 列表回答预留足够空间，可配置：

```text
DEFAULT_PROVIDER=deepseek
DEEPSEEK__MODEL=deepseek-v4-flash
DEEPSEEK__MAX_OUTPUT_TOKENS=4096
DEEPSEEK__THINKING_ENABLED=false
```

普通 RAG 回答默认关闭思考模式，避免不可见的推理内容提前耗尽正文输出预算。即使厂商因为
长度上限返回 `finish_reason=length`，系统也会把消息标记为可重试失败，而不是保存成完整答案。

修改后需要重启后端。知识库搜索接口和会话 RAG 会同时切换；模型故障会返回明确的服务不可用，
不会自动退回阈值并伪装成正常答案。若要停止真实调用，应把配置改回 `threshold` 后重启。

### 2. 启动基础设施并迁移数据库

```powershell
docker compose -f infra/docker/docker-compose.yml up -d
Set-Location backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

### 3. 启动后端、Worker、Beat 和前端

Windows 下 psycopg 异步驱动需要 Selector 事件循环，因此 API 使用项目提供的本地启动
入口；Celery 任务内部也会通过同一兼容运行器进入异步 Service：

```powershell
# 终端一：backend 目录
.\.venv\Scripts\python.exe -m app.local_server

# 终端二：backend 目录；Windows 使用 solo pool，同时消费摘要和文档队列
.\.venv\Scripts\celery.exe -A app.infrastructure.task_queue.celery_app:celery_app worker --loglevel=INFO --pool=solo --queues=conversation-summary,document-ingestion

# 终端三：backend 目录；周期恢复遗失或到期任务
.\.venv\Scripts\celery.exe -A app.infrastructure.task_queue.celery_app:celery_app beat --loglevel=INFO

# 终端四：仓库根目录
Set-Location frontend
npm install
npm run dev
```

只体验基础聊天时可以暂不启动 Worker 和 Beat；上传文档并自动入库时必须启动它们。
PostgreSQL 保存任务真实状态，Redis 只负责传送 Celery 消息。

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)，先注册账号，再体验会话、流式
回复和会话管理。后端健康检查为 [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)，
正确响应中的 `data.status` 为 `ok`。

当助手回答使用了知识库证据时，会在正文下展示引用卡片；实时生成与刷新后的历史消息都会保留
文件名、页码、标题路径和原文快照。Vue 知识库页面已经支持列表、创建、编辑、软归档和恢复；文档
管理区域支持上传、状态轮询、失败重试和可恢复删除。会话详情页可以选择最多 5 个活动知识库并原子
保存绑定；无绑定会话继续使用普通聊天，已归档旧绑定会保留提示但不参与检索。

文档接口支持异步上传、状态查询、失败后新建任务重试，以及使用 `DELETED` 墓碑协调 PostgreSQL 与
MinIO 的可恢复两阶段删除。入库执行期间拒绝删除，避免迟到 Worker 覆盖删除状态。
前端通过 Pinia 管理文档异步状态和可靠轮询；知识库卡片内可以上传 PDF、TXT、Markdown、Word
DOCX、Excel XLSX，查看
等待、处理、可检索和失败状态，并执行失败重试及带确认的可恢复删除。

RAG 原文件保存在 MinIO。控制台地址为
[http://127.0.0.1:9001](http://127.0.0.1:9001)，本地默认账号和密码均为
`minioadmin`，只能用于本机开发。正式环境必须通过环境变量替换默认凭据。

完成后可停止应用进程；如需停止基础设施但保留数据库数据：

```powershell
docker compose -f infra/docker/docker-compose.yml stop
```

`stop` 只停止容器，PostgreSQL 和 Redis 仍会保留在 Docker Desktop 列表中，之后可以
点击启动。`docker compose down` 会移除容器但默认保留命名数据卷；除非明确需要重建
容器，否则本地开发不使用 `down`，也不要执行会删除数据卷的 `down -v`。
