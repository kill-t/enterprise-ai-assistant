
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
