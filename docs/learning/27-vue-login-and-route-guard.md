# Vue 登录页面、会话恢复与路由守卫

## 1. 本模块解决什么问题

第 26 模块已经能调用登录接口并保存 Token，但还没有真实登录入口，也没有阻止未登录用户进入应用页面。

本模块完成：

```text
登录页面
→ Pinia 登录状态
→ sessionStorage 短期 Token
→ /auth/me 会话恢复
→ Vue Router 全局守卫
→ 受保护工作台
```

同时处理四种用户状态：

| 状态 | 系统行为 |
|---|---|
| 没有 Token | 访问受保护页面时跳转登录 |
| Token 有效 | 恢复用户并进入工作台 |
| Token 返回 401 | 清理会话并跳转登录 |
| 临时网络错误 | 保留 Token，允许进入壳页面并显示警告 |

## 2. 完整调用链

首次访问 `/`：

```text
main.ts
→ 创建 Pinia
→ createAppRouter(pinia)
→ 初始导航 /
→ router.beforeEach
→ authStore.ensureSession
→ 检查 sessionStorage Token
→ 必要时 GET /api/v1/auth/me
→ 决定继续或重定向 /login
→ RouterView 渲染页面
```

登录提交：

```text
LoginView 表单
→ 前端格式校验
→ authStore.login
→ authApi.login
→ requestJson
→ POST /api/v1/auth/login
→ 保存 Access Token
→ router.replace(原访问地址或 /)
→ 守卫调用 /auth/me 恢复用户
→ HomeView
```

## 3. 为什么路由工厂显式接收 Pinia

路由守卫运行在 Vue 组件外，却需要读取身份 Store。

当前设计：

```typescript
const pinia = createPinia();
const router = createAppRouter(pinia);
```

守卫内部使用：

```typescript
const authStore = useAuthStore(pinia);
```

优点：

- 守卫与组件使用同一个 Pinia 实例。
- 测试可以传入全新的 Pinia 和 memory history。
- 不依赖隐式全局初始化顺序。
- 不需要在守卫里使用旧式 `next()` 回调。

Vue Router 官方支持异步全局守卫直接返回重定向位置，这比多分支调用 `next()` 更不容易出现重复调用。[Vue Router Navigation Guards](https://router.vuejs.org/guide/advanced/navigation-guards.html)

## 4. 前端路由守卫不是权限系统

路由守卫只能控制当前浏览器显示哪个页面。用户仍然可以：

- 手动调用后端 API。
- 修改前端 JavaScript。
- 删除浏览器里的路由判断。

因此真正权限必须在后端：

```text
FastAPI 验证 JWT
→ 获取 current_user
→ Repository 查询带 user_id 所有权条件
→ 不属于用户的数据返回 404/403
```

前端守卫解决的是用户体验，后端认证和所有权过滤解决安全问题。面试中必须明确区分这两层。

## 5. requiresAuth 和 guestOnly

路由元数据表达页面规则：

```text
home.requiresAuth = true
login.guestOnly = true
```

守卫判断：

```text
requiresAuth + 未登录
→ /login?redirect=原地址

guestOnly + 已登录
→ /
```

保存 `redirect` 能让用户登录后回到原来准备访问的页面，而不是永远回首页。

## 6. 为什么 redirect 必须检查

不能直接信任 URL 中的：

```text
/login?redirect=...
```

如果允许任意地址，攻击者可能构造登录后跳到恶意站点的开放重定向。

当前只接受：

- 字符串。
- 以单个 `/` 开头的站内路径。
- 拒绝 `//evil.example.com` 协议相对地址。

不满足规则时统一回到 `/`。

## 7. ensureSession 为什么只执行一次

如果每次路由跳转都请求 `/auth/me`：

```text
首页 → 会话页 → 设置页
→ 连续三次 /auth/me
```

这会增加延迟和无价值请求。

Store 使用：

```text
sessionInitialized = false / true
```

首次导航验证一次，后续导航直接使用已恢复的状态。登录成功会重新设为未初始化，使下一次进入受保护页面验证新 Token；退出登录则直接进入“已知未登录”状态。

## 8. 401 和网络错误为什么不同

### 401

后端已经明确告诉前端：

```text
Token 无效、过期或用户不可用
```

所以应当：

```text
清理 Pinia
→ 清理 sessionStorage
→ 跳转登录
```

### NETWORK_ERROR

网络错误只说明当前无法连接后端，不能证明 Token 无效。

如果遇到一次断网就清理 Token，用户网络恢复后仍需重新登录。因此当前策略是：

```text
保留 Token
→ 允许进入受保护壳页面
→ 显示“暂时无法验证”
→ 后续真实 API 请求仍由后端验证
```

这是可用性与严格在线验证之间的取舍。需要强制在线的高风险系统可以在网络错误时阻止导航，但仍不应谎称 Token 已失效。

## 9. 登录表单负责什么

LoginView 只承担页面交互：

- 双向绑定邮箱和密码。
- 基础邮箱格式检查。
- 密码至少 8 位。
- 邮箱去除空格并转为小写。
- 提交时禁用输入框和按钮。
- 显示后端安全中文错误。
- 登录成功后安全跳转。

它不直接调用 `fetch`，而是调用 Pinia action：

```text
LoginView
→ authStore.login
→ authApi.login
→ requestJson
```

组件测试因此只关心用户可见输入、输出和跳转，不测试内部 ref 名称。

## 10. 为什么登录中必须禁用按钮

用户连续点击登录可能产生：

```text
请求 A
请求 B
请求 C
```

响应顺序不一定与发送顺序相同，还会增加限流和日志噪音。

Store 的 `loading` 在 `try/finally` 中维护：

```text
开始请求 → loading = true
成功或失败 → finally → loading = false
```

页面在 loading 时：

- 输入框不可修改。
- 登录按钮不可重复点击。
- 按钮显示“登录中…”。

失败后 `finally` 保证按钮恢复。

## 11. 为什么 Token 使用 sessionStorage

当前第一版没有 Refresh Token 和 HttpOnly Cookie，因此短期 Access Token 保存在当前标签页：

- 刷新可以恢复。
- 关闭标签页自动清除。
- 不保存密码。

但 `sessionStorage` 仍可被同源 JavaScript 读取，不能抵御 XSS。必须继续避免不可信 HTML、控制依赖并设置安全响应头。以后如采用 HttpOnly Cookie，还需要一起设计 CSRF 防护。

## 12. 测试保护什么

路由测试：

- 无 Token 访问 `/` 会跳转 `/login`。
- 重定向查询参数保留原地址。
- 有效 Token 调用 `/auth/me` 并离开登录页。
- 401 清理 Pinia 和 sessionStorage。
- 临时断网保留 Token 和受保护页面。

组件测试：

- 短密码不调用登录接口。
- 邮箱标准化后再提交。
- 登录期间按钮禁用并显示加载文字。
- 成功后跳转安全 redirect。
- 后端中文错误显示在 `role=alert`。
- 失败后按钮恢复可用。

Vue Test Utils 官方建议从用户可见的输入、DOM 输出和副作用测试组件，而不是绑定实现细节；本模块使用 `data-test` 只定位输入控件。[Vue Test Utils：易测试组件](https://test-utils.vuejs.org/guide/essentials/easy-to-test.html)

## 13. 浏览器验收与一次真实 CSS 错误

真实浏览器验证了：

- 访问 `/` 自动跳转 `/login?redirect=/`。
- 标题、输入框、标签和按钮可访问。
- 输入短密码显示“密码至少需要 8 位”。
- 校验失败不离开登录页。
- 控制台没有错误或警告。

第一次截图发现右侧表单被截断。原因不是 Vue，而是 CSS Grid 子项默认的最小内容宽度：440px 表单加上面板 padding，迫使网格超过视口。

修复方式：

```css
.auth-story,
.auth-panel {
  min-width: 0;
}

.login-form {
  width: 100%;
  max-width: 440px;
}
```

修复后浏览器测得：

```text
document.scrollWidth <= window.innerWidth
```

因此没有横向溢出。这说明组件测试和构建不能代替真实布局检查。

## 14. 本模块没有数据库迁移

本模块只增加前端路由、页面和状态恢复，没有修改数据库结构，不需要 Alembic migration。

## 15. 当前还没有实现什么

- 注册页面。
- 会话列表、创建、归档和删除。
- 消息历史与流式聊天组件。
- AbortController 停止按钮。
- 真实后端账号的登录端到端测试。
- Refresh Token 或 HttpOnly Cookie。
- 生产 Nginx history fallback 配置。

当前浏览器测试没有提交真实账号密码；完整登录 E2E 会在可复现演示账号和 Docker Compose 数据准备完成后加入。

## 16. 常见错误

- 把 Vue Router 守卫当作真正权限控制。
- 每次路由跳转都调用 `/auth/me`。
- 任何请求失败都清除 Token。
- 使用旧式 `next()` 并在多个分支重复调用。
- 登录成功后直接使用不可信 redirect 跳转。
- 登录中不禁用按钮，产生重复请求。
- 组件直接调用 fetch，重复协议代码。
- 只写单元测试，不在真实浏览器检查布局。
- Grid 子项不设置 `min-width: 0`，导致内容撑破视口。

## 17. 面试表达

> 我把认证拆成 API、Pinia 状态和 Vue Router 守卫。路由工厂显式接收 Pinia，首次导航通过 ensureSession 只验证一次短期 Token。401 表示明确失效，会清理会话；NETWORK_ERROR 只表示暂时离线，所以保留 Token 并显示警告。受保护路由和 guest-only 路由由 meta 驱动，登录 redirect 只允许站内安全路径。我也会强调前端守卫只改善导航体验，真正授权仍由 FastAPI JWT 和数据所有权查询保证。

## 18. 小练习

请解释为什么下面处理不正确：

```text
/auth/me 请求失败
→ 不看错误类型
→ 立即删除 Token
```

答案要点：401 能证明 Token 无效，但断网和服务器暂时不可用不能；如果全部删除，会让一次临时网络问题变成不必要的强制退出。
