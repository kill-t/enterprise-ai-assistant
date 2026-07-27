ADR-002-configuration-structure.md
# ADR-002: Configuration Structure

## Status

Accepted

---

## Context

The Enterprise AI Assistant project contains multiple infrastructure components:

- PostgreSQL
- Redis
- LLM Providers
- Vector Database (Qdrant)
- Object Storage (MinIO)
- JWT Authentication
- Monitoring
- External APIs

As the project grows, configuration items will increase rapidly.

Using a single flat configuration file (e.g. `database_url`, `redis_url`, `api_key`, `qdrant_url`, `minio_endpoint`...) makes the Settings class difficult to maintain and understand.

---

## Decision

The project adopts **Domain-based Configuration**.

Configurations are grouped according to business domains instead of placing every configuration field inside a single Settings class.

Example:

```python
Settings
├── DatabaseSettings
├── RedisSettings
├── LLMSettings
├── QdrantSettings
├── MinIOSettings
└── JWTSettings

Each infrastructure component owns its own configuration model.

Environment Variable Strategy

Nested configuration uses double underscores (__).

Example:

DATABASE__URL=postgresql+psycopg://...
REDIS__URL=redis://localhost:6379/0

LLM__API_KEY=xxxxxxxx
LLM__BASE_URL=https://api.openai.com/v1
LLM__MODEL=gpt-5

This keeps environment variables organized and naturally maps to nested configuration objects.

Configuration Loading

Configuration is managed using Pydantic Settings (v2).

Benefits include:

Environment variable loading
Type validation
Default values
IDE autocompletion
Automatic error reporting for invalid configuration
Singleton Strategy

Settings are loaded only once during application startup.

The project uses functools.lru_cache() to cache the Settings instance.

Benefits:

Avoid repeated parsing of environment variables
Better performance
Consistent configuration across the application
Easier dependency injection and testing
Consequences
Advantages
Clear separation of configuration domains.
Easy to extend when introducing new infrastructure.
Better readability and maintainability.
Type-safe configuration.
Suitable for enterprise-scale applications.
Trade-offs
Slightly more complex than a flat Settings class.
Developers need to understand nested environment variables (__).
Future Extensions

The configuration structure is expected to grow as the project evolves.

Planned configuration groups include:

Database
Redis
LLM
Qdrant
MinIO
JWT
Logging
Monitoring
MCP
Agent Configuration

---

## 为什么我要你写 ADR？

很多 GitHub 项目只有代码。

优秀的项目会有：

```text
docs/

architecture/

ADR/

project-context.md

README.md

API.md

deployment.md

以后面试官问：

为什么配置这样设计？

你甚至可以打开 ADR 给他看。

这就是工程能力。