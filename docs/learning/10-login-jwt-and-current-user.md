# 模块 10：登录、JWT 与当前用户

## 要解决的问题

注册完成后，系统需要验证用户身份并给后续请求一个可验证的登录凭证。本模块增加 OAuth2 Password
表单登录、JWT Access Token 和当前用户接口：

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
```

第一版只签发短期 Access Token，不实现 Refresh Token。等安全边界和前端登录流程稳定后，再单独
设计刷新、吊销和多设备会话。

## 登录调用链

```text
OAuth2 表单 username（填写邮箱）+ password
→ AuthService.login
→ UserRepository.get_by_email
→ PasswordHasher.verify
→ TokenService.create_access_token
→ TokenResponse
```

登录只读取用户，不修改业务状态，因此不需要数据库 `commit()`。

## JWT 中保存什么

Access Token 只保存身份验证需要的声明：

- `sub`：用户 UUID。
- `type`：固定为 `access`。
- `iat`：签发时间。
- `exp`：过期时间。

JWT 是签名数据，不是加密数据。客户端和第三方可以读取载荷，所以不能把密码、密码哈希、API Key
或其他敏感正文放进 Token。

验证时必须固定允许的算法，不能直接相信 Token 头部声明的算法。本项目第一版固定使用 HS256，并
要求密钥至少 32 字节。

## 当前用户调用链

```text
Authorization: Bearer <token>
→ OAuth2PasswordBearer
→ TokenService 验证签名、类型和过期时间
→ 从 sub 恢复用户 UUID
→ UserRepository.get_by_id
→ 检查用户仍存在且处于启用状态
→ UserResponse
```

即使 Token 有效，系统仍查询 PostgreSQL。这样用户被删除或停用后，旧 Token 不会继续获得访问权。
以后若性能需要，可以短暂缓存用户状态，但 PostgreSQL 仍是最终事实来源。

## 防止账号枚举

不存在邮箱和错误密码都返回：

```text
401 INVALID_CREDENTIALS：邮箱或密码错误
```

用户不存在时仍会对固定 dummy Argon2 哈希执行一次校验，使两条失败路径的计算量更接近，降低攻击者
通过响应时间判断邮箱是否注册的风险。

停用账号只有在密码正确后才返回 `403 ACCOUNT_DISABLED`。所有 401 认证错误都带
`WWW-Authenticate: Bearer` 响应头。

## 配置

`.env` 必须设置：

```dotenv
AUTH__SECRET_KEY=至少32字节的随机密钥
AUTH__ALGORITHM=HS256
AUTH__ACCESS_TOKEN_EXPIRE_MINUTES=30
```

代码不提供可用于生产的默认密钥。缺少密钥时，注册仍可使用，但登录和 Token 解析会明确失败。

## 测试覆盖

- Token 签发后可以恢复原用户 UUID。
- 过期和篡改 Token 被拒绝。
- 正确密码签发 Bearer Token。
- 不存在用户和错误密码返回相同错误。
- 停用用户不能登录。
- `/me` 从数据库恢复当前用户且不返回密码哈希。
- OAuth2 表单和 OpenAPI 路径符合接口约定。

## 面试表达

“JWT 只保存用户 UUID 和时间声明，固定验证算法且设置过期时间。每次恢复当前用户仍查询数据库，
所以账号停用立即生效。登录失败使用统一提示和 dummy 哈希降低账号枚举风险；401 响应遵循 Bearer
协议返回认证头。”

## 小练习

解释为什么 `/me` 不能只相信 JWT 中的 `sub`，而完全不查询数据库。

答案要点：JWT 签发后，用户可能已被停用或删除；如果只相信 Token，权限变化要等到 Token 过期才
生效。查询数据库可以得到当前账号状态，也是后续组织权限和角色校验的入口。
