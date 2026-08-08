# 本地联调底座与注册自动登录

## 1. 本模块解决什么问题

此前单元测试通过，但真实启动暴露了四个问题：健康检查返回 500、Windows 下
psycopg 异步连接无法使用默认事件循环、JWT 密钥缺失要到第一次登录才发现、前端
没有注册入口。

本模块把它们收口，使新开发者可以完成：

```text
启动服务 → 注册 → 自动登录 → 进入工作台 → 创建会话
```

## 2. 两条关键调用链

### 注册后自动登录

```text
RegisterView 表单
→ AuthStore.registerAndLogin
→ POST /api/v1/auth/register
→ POST /api/v1/auth/login
→ sessionStorage 保存 Access Token
→ Router 跳转 redirect 或首页
```

注册接口只创建用户并返回公开字段；JWT 仍只由登录接口签发。前端不自行生成 Token，
避免把认证规则分散到浏览器。

### 服务启动校验

```text
python -m app.local_server
→ 选择 Windows Selector 事件循环
→ Uvicorn 启动 FastAPI
→ lifespan 调用 AuthSettings.validate_runtime
→ 密钥合法才开始接收 HTTP 请求
```

Windows 上默认 `ProactorEventLoop` 与 psycopg 异步连接不兼容。`local_server` 仅服务于
本地开发，生产容器仍可使用标准 Uvicorn 启动方式。

## 3. 为什么 JWT 密钥要在启动时检查

JWT 密钥缺失时，注册接口可能正常，但登录会在依赖注入阶段返回 500。这样的错误离
根因很远，也让健康检查失去意义。

当前 `AuthSettings.validate_runtime()` 要求：

- `AUTH__SECRET_KEY` 必须存在；
- UTF-8 编码后至少 32 字节。

密钥只存在于未提交的 `backend/.env`。`.env.example` 只提供变量名和说明，不能放入
真实密钥。

## 4. 页面与路由边界

`/login` 和 `/register` 都是 `guestOnly`：

- 未登录用户可以访问；
- 已登录用户访问时回到首页；
- 登录页会把安全的 `redirect` 参数传给注册页；
- 注册页完成自动登录后复用同一条安全跳转规则。

只允许以单个 `/` 开头、且不以 `//` 开头的站内路径，避免开放重定向到外部网站。

## 5. 健康检查为什么也使用统一响应

`ApiResponse` 需要 `code`、`message` 和 `data`。直接构造 `ApiResponse(data=...)` 会触发
Pydantic 校验错误；现在通过：

```python
ApiResponse.success(data={"status": "ok"})
```

返回：

```json
{"code":"SUCCESS","message":"success","data":{"status":"ok"}}
```

## 6. 测试保护什么

后端测试覆盖健康检查成功结构、缺失或过短 JWT 密钥、生命周期启动前校验，以及
Windows/非 Windows 的事件循环选择。

前端测试覆盖注册 JSON 协议、注册后自动登录并保存 Token、注册表单校验和错误展示、
访客路由守卫及登录页到注册页的 redirect 传递。

这些测试没有真实调用模型。手动联调时设置 `DEFAULT_PROVIDER=mock`，仍可验证数据库、
Redis、SSE 与 Vue 状态协作，但不会消耗外部模型额度。

## 7. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 后端启动即失败，提示 `AUTH__SECRET_KEY` | 在本机 `.env` 填写至少 32 字节随机值。 |
| Windows 下请求数据库时报 ProactorEventLoop | 使用 `python -m app.local_server`，而不是直接运行 Uvicorn。 |
| 登录后马上回到登录页 | 检查 JWT 密钥是否在后端重启前已生效，以及浏览器是否保存了旧 Token。 |
| 想演示但不想花模型额度 | 保持 `DEFAULT_PROVIDER=mock`。 |

## 8. 面试表达

> 我不会把“能跑测试”当作本地可运行。真实联调暴露了 Windows 异步数据库事件循环、JWT
> 配置延迟失败和前端缺少注册入口的问题。我增加了启动前配置校验、Windows 专用本地
> Runner，以及注册后复用登录接口签发 Token 的流程。这样认证规则仍在后端，前端只保存
> 短期 Access Token 并完成安全站内跳转。

## 9. 小练习

请为注册页补一条测试：当 `redirect=//example.com` 时，注册成功后必须跳转首页，而不能
跳转到外部地址。

答案要点：使用 `/register?redirect=//example.com` 挂载页面，mock 注册和登录成功，提交
表单后断言 Router 当前路径为 `/`。
