# 模块 09：密码安全与用户注册

## 要解决的问题

用户表建立后，系统仍不能直接保存客户端传来的密码。注册流程必须完成输入校验、邮箱标准化、密码
哈希、重复邮箱处理和事务提交，同时保证响应、日志和数据库都不泄露明文密码。

本模块提供：`POST /api/v1/auth/register`。登录和 JWT 留到下一模块。

## 调用链

```text
HTTP JSON
→ UserRegisterRequest
→ AuthService.register
→ PasswordHasher
→ UserRepository
→ AsyncSession.commit
→ UserResponse
```

## 每层负责什么

### Schema

- 使用 `EmailStr` 校验邮箱格式。
- 将邮箱统一转换为小写。
- 密码长度限制为 8～128 个字符。
- `UserResponse` 不定义 `password` 或 `password_hash`，从响应模型边界阻止泄露。

### PasswordHasher

使用 `pwdlib` 当前推荐的 Argon2 算法。Argon2 是有意设计得较慢、较耗资源的密码哈希算法，目的是
提高攻击者批量猜测密码的成本。哈希和校验属于 CPU 密集操作，因此封装在 `asyncio.to_thread()`
中，避免阻塞 FastAPI 事件循环。

### AuthService

- 注册前查询邮箱，尽早返回友好错误。
- 等确认邮箱未使用后再执行高成本的 Argon2 哈希。
- 调用 Repository 创建用户。
- 统一提交或回滚事务。
- 把数据库唯一约束冲突转换为稳定的 `EMAIL_ALREADY_REGISTERED` 业务错误。

### Repository

Repository 仍然只访问数据库，不知道明文密码、HTTP 状态码和注册规则，也不自行提交事务。

## 为什么查询后还可能出现重复邮箱

并发请求可能按下面的顺序执行：

```text
请求 A 查询：邮箱不存在
请求 B 查询：邮箱不存在
请求 A 插入并提交
请求 B 插入 → 唯一约束冲突
```

所以应用查询是为了减少无意义的哈希并提供友好提示，数据库唯一约束才是最终正确性边界。Service
必须捕获 `IntegrityError`、回滚 Session，再返回 409；如果不回滚，Session 后续不能继续使用。

## 异步 HTTP 测试

接口测试使用 `httpx.AsyncClient` 和 `ASGITransport` 直接请求 FastAPI 应用，不启动真实端口。通过
FastAPI 的依赖覆盖注入 Mock AuthService，从而只验证 HTTP 协议、请求校验和响应过滤，不连接数据库。

这类测试属于接口集成测试；AuthService 的重复邮箱和事务测试仍是单元测试。

## 测试覆盖

- 哈希不等于明文，并使用 Argon2 格式。
- 正确密码校验成功，错误密码校验失败。
- 邮箱转换为小写，短密码被拒绝。
- 注册成功只向 Repository 传递哈希。
- 已存在邮箱不会执行密码哈希。
- 并发唯一约束冲突会回滚并返回稳定业务错误。
- HTTP 接口返回 201，响应不包含任何密码字段。

## 面试表达

“密码哈希使用 Argon2，并把 CPU 密集计算隔离到线程，避免阻塞异步事件循环。重复邮箱同时由应用
预检查和数据库唯一约束保护；并发冲突发生后会显式回滚 Session。响应模型从结构上排除了密码
哈希字段。”

## 小练习

解释为什么下面两个操作的顺序不能反过来：

```text
先查询邮箱是否已存在
再执行 Argon2 密码哈希
```

答案要点：Argon2 为了安全有意消耗较多 CPU 和内存。明显重复的邮箱应尽早拒绝，避免消耗有限的
哈希资源；但数据库唯一约束仍必须保留以处理并发竞争。
