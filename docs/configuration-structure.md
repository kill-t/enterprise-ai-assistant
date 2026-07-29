# ADR-002：按领域组织配置

## 状态

已采纳。

## 背景

项目包含 PostgreSQL、Redis、LLM Provider、JWT、MinIO、MCP、监控等基础设施。如果把所有配置
都平铺为 `database_url`、`redis_url`、`api_key` 等字段，Settings 会快速膨胀，也难以判断一项
配置属于哪个边界。

## 决策

使用 Pydantic Settings V2，并按领域拆分配置模型：

```text
Settings
├─ DatabaseSettings
├─ RedisSettings
├─ ProviderSettings
└─ AuthSettings
```

后续按实际实现增加 MinIO、MCP、Worker、可观测性等配置组，不提前创建空配置。

嵌套环境变量使用双下划线：

```dotenv
DATABASE__URL=postgresql://postgres:postgres@localhost:5432/enterprise_ai
REDIS__URL=redis://localhost:6379/0
AUTH__SECRET_KEY=至少32字节的随机密钥
AUTH__ACCESS_TOKEN_EXPIRE_MINUTES=30
```

完整模板见 `backend/.env.example`，真实密钥只写入被 Git 忽略的 `backend/.env`。

## JWT 配置决策

- `AUTH__SECRET_KEY` 没有代码默认值，登录时缺失会明确失败。
- HS256 密钥至少 32 字节。
- 第一版算法固定为 HS256，防止环境变量意外切换到未评审算法。
- Access Token 有效期限制在 1～1440 分钟。
- 配置和日志不得输出 SecretStr 的真实内容。

## 取舍

优点：

- 配置边界清晰，IDE 和 Pydantic 能提供类型校验。
- 环境变量自然映射到领域对象。
- 敏感配置与普通业务配置更容易区分。
- 测试可以独立替换某一领域配置或依赖。

代价：

- 比平铺配置多一层对象访问。
- 开发者必须理解双下划线嵌套规则。
- 新增必填配置时需要同步更新环境模板和部署配置。

## 后果

代码通过缓存后的全局 Settings 读取稳定配置；具体业务组件仍优先通过 FastAPI Dependency 注入，
以便测试替换。新增配置项时必须同步更新 `.env.example`、中文文档和配置测试。
