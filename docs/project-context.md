
# Enterprise AI Assistant 当前进度（2026-08-16）

> 本节是继续开发时优先阅读的最新状态。下方旧 Sprint 内容保留为早期历史记录，不能再作为当前待办。

## 当前阶段

RAG 1.0 核心链路已经完成，正在收口 RAG 1.1 的多格式文档能力。

已经完成：

- PDF、TXT、Markdown 上传、解析、Token 分块和可恢复 Worker 入库。
- Mock 与 OpenAI-compatible Embedding、pgvector 向量检索。
- pg_trgm 关键词检索、RRF 混合召回和可配置 LLM Evidence Selector。
- 会话知识库绑定、Grounded Prompt、无证据拒答、SSE sources 和引用快照。
- Vue 知识库管理、文档状态、会话绑定和引用卡片。
- 助手 Markdown 安全渲染和表格美化。
- `source_type + source_locator` 跨格式结构化来源位置。

当前数据库迁移 head：

```text
d4e8b6c1a209
```

真实旧数据已验证从 `a1c5e9d3f702` 无损升级；用户、会话、消息、Chunk 和引用数量保持不变。

当前回归结果：

```text
后端：445 passed
前端：157 passed
Ruff：通过
Vue 类型检查与生产构建：通过
```

## 下一模块

RAG-9：Word `.docx` Parser。

顺序：

```text
DOCX 上传白名单
→ python-docx Parser Adapter
→ 按正文真实顺序读取段落与表格
→ 标题路径和块编号
→ Token 分块、Embedding 与现有 Worker
→ 引用卡片展示章节/表格位置
→ Parser、入库、检索和权限回归
```

之后依次为：Excel `.xlsx`、扫描 PDF/图片 OCR、多格式评测，然后结束 RAG 1.1 并进入 MCP。

## 继续开发时必须保持的约定

- 中文代码注释、日志、学习文档和 Git 提交。
- 每个模块先讲解，再写接口与测试，最后实现、回归和小练习。
- PostgreSQL 是业务事实来源；Redis 只做可降级缓存。
- 原文件保存在 MinIO，Chunk、向量、任务和引用保存在 PostgreSQL。
- 原始 Markdown 保存在数据库，Vue 负责安全渲染。
- 旧 `heading_path/page_start/page_end` 暂时保留兼容；新格式使用结构化 Locator。
- 不执行 `docker compose down -v`，不删除已有数据卷。
- 工作区未跟踪的 `output/`、`tmp/` 当前不属于功能提交，不要擅自删除或纳入 Git。

---

```md
# Enterprise AI Assistant - Project Context

> Last Updated: 2026-07-17
>
> Purpose:
> This document stores the current project architecture, decisions, progress and future roadmap.
> When continuing development in a new chat session, read this document first.

---

# 1. Project Overview

## Project Name

Enterprise AI Assistant


## Project Goal

Build an enterprise-level AI Assistant platform for learning and demonstrating modern AI application development.

The project is designed for:

- AI Application Developer internship/job preparation
- Learning enterprise AI architecture
- Learning LangChain
- Learning LangGraph Agent development
- Learning RAG systems
- Learning production-level backend engineering


## Main Learning Objectives

The project should cover:

- FastAPI backend development
- Modern Python engineering practices
- LLM application architecture
- LangChain framework
- LangGraph Agent workflow
- RAG pipeline
- Memory management
- Tool calling
- MCP
- Vector database
- Database design
- Deployment


---

# 2. Current Project Status

## Current Phase

Sprint 1 - Backend Infrastructure Initialization


## Completed

### Project Structure

[x] Created workspace:

enterprise-ai-assistant


Current workspace:

```

enterprise-ai-assistant/

├── backend/
├── frontend/
├── docker/
├── docs/
└── scripts/

```


### Python Environment

[x] Backend is an independent Python project.

[x] Using uv for dependency management.

[x] Python version:

```

Python 3.12

````


### Backend Initialization

Completed:

[x] uv init inside backend

[x] Created virtual environment inside backend

[x] Installed:

- fastapi
- uvicorn
- pydantic-settings
- pytest
- ruff


### FastAPI

Completed:

[x] FastAPI application initialized.

Current startup command:

```bash
uv run uvicorn app.main:app --reload
````

### Configuration Management

Completed:

Using:

```
pydantic-settings
```

Current structure:

```
app/core/config.py
```

Purpose:

* Manage environment variables
* Separate configuration from code
* Support different environments

### Logging

Completed:

Basic Python logging system added.

Current structure:

```
app/core/logging.py
```

Purpose:

Replace print debugging.

Support:

* INFO
* ERROR
* DEBUG

### Application Lifecycle

Completed:

FastAPI lifespan architecture introduced.

Purpose:

Manage startup/shutdown resources.

Future usage:

* Database connection initialization
* Redis initialization
* LLM client initialization
* Vector database initialization

---

# 3. Current Backend Structure

Current target architecture:

```
backend/

├── app/
│
│   ├── main.py
│
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── future core modules
│
│   ├── common/
│   │
│   ├── infrastructure/
│   │
│   └── modules/
│
├── tests/
│
├── .env
├── pyproject.toml
└── uv.lock
```

---

# 4. Architecture Decisions

## Decision 1

## Backend is an independent Python project

Reason:

The repository is a workspace, not a single project.

Future:

```
enterprise-ai-assistant/

backend/
frontend/
mobile/
docs/
infra/
```

Each part can evolve independently.

---

# Decision 2

## Use uv instead of requirements.txt

Reason:

Modern Python project management.

Advantages:

* Fast dependency installation
* pyproject.toml based
* Lock file support
* Better reproducibility

Dependency management:

Use:

```bash
uv add package
```

instead of:

```bash
pip install package
```

---

# Decision 3

## Modular architecture instead of global services/models folders

Avoid:

```
services/

models/

repositories/
```

Reason:

Large AI applications become difficult to maintain.

Use:

```
modules/

    chat/

    knowledge/

    agent/

    auth/

```

Each module owns its:

* API
* Service
* Repository
* Schema
* Business logic

---

# Decision 4

## Separate infrastructure from business logic

External technologies should not directly leak into business code.

Examples:

Bad:

```
ChatService

    RedisClient()

    OpenAI()

    MilvusClient()
```

Good:

```
ChatService

        ↓

Abstract Interface

        ↓

Infrastructure Implementation
```

Reason:

Allow replacement:

* OpenAI -> DeepSeek
* Milvus -> Qdrant
* Redis -> other cache

without modifying business code.

---

# 5. Planned Architecture

High-level:

```
                 Client

                   |

              FastAPI API

                   |

            Application Layer

                   |

             Agent / Workflow

                   |

        Infrastructure Layer

                   |

 PostgreSQL / Redis / LLM / Vector DB
```

---

# 6. Technology Stack

## Backend

Language:

* Python 3.12

Framework:

* FastAPI

Package Management:

* uv

Validation:

* Pydantic

Testing:

* Pytest

Code Quality:

* Ruff

---

## AI Stack

Planned:

* LangChain
* LangGraph
* LLM Provider abstraction
* Prompt management
* Structured output
* Tool calling

---

## Database

Planned:

Relational database:

* PostgreSQL

Cache:

* Redis

Vector database:

Initial choice:

* TBD

Candidates:

* Qdrant
* Milvus
* pgvector

Selection principle:

Choose based on enterprise suitability, not popularity only.

---

# 7. LangChain / LangGraph Learning Direction

The project will use LangChain mainly for:

* Model abstraction
* Prompt management
* Tool integration
* Structured output
* Retrieval components

LangGraph will be responsible for:

* Agent workflow
* State management
* Node execution
* Memory handling
* Human-in-the-loop

Expected architecture:

```
User Request

      |

FastAPI

      |

LangGraph Agent

      |

Nodes

      |

Tools / RAG / LLM

```

---

# 8. Development Roadmap

## Sprint 1 - Backend Foundation

Status:

IN PROGRESS

Tasks:

[x] uv project initialization

[x] FastAPI initialization

[x] Configuration management

[x] Logging system

[x] Lifespan system

Next:

* Exception handling
* Unified API response format
* Router organization

---

# Sprint 2 - Database Infrastructure

Planned:

* Docker compose
* PostgreSQL
* SQLAlchemy 2.0
* Alembic
* Repository pattern

---

# Sprint 3 - LLM Integration

Planned:

* LLM provider abstraction
* ChatModel wrapper
* Prompt templates
* Structured output

---

# Sprint 4 - LangChain

Learn and implement:

* Chains
* Runnable
* Prompt
* Output parser
* Retriever

---

# Sprint 5 - LangGraph Agent

Implement:

* State
* Node
* Edge
* Graph
* Agent workflow

---

# Sprint 6 - RAG System

Implement:

* Document ingestion
* Chunking
* Embedding
* Vector storage
* Retrieval
* Reranking

---

# Sprint 7 - Advanced Agent Features

Implement:

* Tool calling
* Memory
* MCP
* Human approval workflow

---

# Sprint 8 - Deployment

Implement:

* Docker
* CI/CD
* Monitoring
* Production configuration

---

# 9. Coding Rules

## API Layer

Responsible for:

* Request validation
* Response formatting
* Calling service

Should NOT contain:

* Database logic
* AI logic

---

## Service Layer

Responsible for:

* Business orchestration
* Calling Agent
* Calling infrastructure

---

## Infrastructure Layer

Responsible for:

* Database
* Redis
* LLM client
* Vector database

---

# 10. Current Task

Current unfinished task:

Sprint 1 Day 5

Need to finish:

1. Logging verification

2. FastAPI lifespan verification

3. Exception handling

4. Unified API response model

After that:

Move to database infrastructure.

---

# 11. Important Notes for Future Sessions

When continuing this project:

Do not redesign architecture unless necessary.

Current architecture decisions:

* uv based Python project
* Modular architecture
* Infrastructure isolation
* FastAPI backend
* LangGraph as Agent orchestration layer

Development style:

Explain:

1. Why
2. Architecture
3. Code
4. Test
5. Interview explanation

The goal is not only to complete the project,
but to understand enterprise AI application engineering.

已完成的任务：
## Sprint 3

### Completed

- Implemented ConversationEntity
- Configured Alembic migration
- Created ConversationRepository
- Created ConversationService
- Implemented POST /api/v1/conversations
- Completed first end-to-end API flow

### Architecture

Router
↓
Service
↓
Repository
↓
Entity
↓
PostgreSQL

````

---

建议你再额外做一个小优化：

在 `docs/` 下以后维护两个文件：

```text
docs/

├── project-context.md       # 给 AI / 新成员看的项目状态
│
└── architecture.md          # 专门记录架构设计
````

区别：

* `project-context.md`：**当前进度**
* `architecture.md`：**为什么这样设计**

这样以后项目做到 LangGraph、RAG、MCP 的时候，不会混乱。

下一次即使换新对话，你上传这个文件，我们就可以直接从：

> Sprint 1 Day 5：异常处理 + API Response 规范

继续。你不用重新解释整个项目。
