# Vue3 请求、认证与 SSE 协议底座

## 1. 本模块解决什么问题

此前 `frontend` 只有一个 `.gitkeep`，浏览器还不能调用已经完成的 FastAPI 接口。

本模块没有急着写完整聊天页面，而是先建立前端的协议底座：

```text
Vue3 + TypeScript + Vite
→ Pinia 身份状态
→ 统一 fetch 请求客户端
→ 增量 SSE 文本解析
→ started/delta/completed/error 业务事件校验
```

这样下一模块实现登录和聊天组件时，只需要消费明确的函数和状态，不需要在 Vue 组件里重复处理 HTTP、JSON、SSE 分块和错误转换。

## 2. 为什么先做协议层，不先画聊天页面

如果组件直接承担所有工作：

```text
ChatView.vue
├─ 拼 URL
├─ 加 Bearer Token
├─ 判断 HTTP 错误
├─ 读取 ReadableStream
├─ 拆 SSE 帧
├─ JSON.parse
├─ 校验事件顺序
└─ 修改消息气泡
```

页面会很快变成难测试的大组件。

当前分层是：

```text
Vue Component：展示和用户交互
Pinia Store：跨组件身份状态
API Module：后端接口语义
HTTP Client：认证头和错误边界
SSE Parser：字节流到通用事件
Contracts：前后端 TypeScript 数据契约
```

每层只解决一种问题，也能单独写测试。

## 3. 工程技术选择

本模块使用：

- Vue3 Single File Component 和 `<script setup>` 后续开发方式。
- TypeScript 严格模式。
- Vite 开发与生产构建。
- Pinia 管理身份状态。
- Vitest + jsdom 做单元测试。
- 原生 `fetch` 和 `ReadableStream`。

没有引入 Axios，因为浏览器原生 `fetch` 已能同时处理普通 JSON 和流式响应。少一个请求库也能更直接地理解浏览器流协议。

当前 Node.js 是 24，满足当前 Vue/Vite 工具链要求。安装时 npm 默认选择了 TypeScript 7，但 `vue-tsc 3.3.9` 无法使用该版本的内部入口，因此项目把 TypeScript 固定在兼容的 `5.9.x`。这是一次真实的依赖兼容调试：最新版本组合不一定能共同运行，必须由类型检查和构建验证。

## 4. 普通 HTTP 调用链

例如获取当前用户：

```text
Vue / Pinia
→ getCurrentUser(token)
→ requestJson<UserResponse>
→ requestApi
→ 添加 Authorization: Bearer ...
→ fetch /api/v1/auth/me
→ 检查 HTTP 状态
→ 解析 JSON
→ 返回 UserResponse
```

`requestApi` 负责：

- 拼接可选 `VITE_API_BASE_URL`。
- 设置 `Accept`。
- 添加 Bearer Token。
- JSON 字符串请求设置 `Content-Type`。
- 把非 2xx 转成 `ApiError`。
- 把普通断网转成 `NETWORK_ERROR`。
- 让 `AbortError` 原样传播。

`requestJson` 再负责解析成功 JSON。

## 5. 为什么 AbortError 不能包装成网络错误

下一模块的停止按钮会调用：

```typescript
controller.abort();
```

浏览器会让 `fetch` 抛出名称为 `AbortError` 的异常。这是用户主动操作，不是网络故障。

当前请求层区分：

```text
AbortError
→ 原样抛出
→ 页面显示“已停止”

TypeError 等 fetch 故障
→ ApiError(code=NETWORK_ERROR, status=0)
→ 页面显示网络错误和重试
```

如果把二者统一包装，停止按钮每次都会错误显示“网络连接失败”。

## 6. HTTP 错误和 SSE error 不是同一种错误

流式接口存在两个阶段：

### started 之前

```text
会话不存在
→ HTTP 404
→ application/json
→ requestApi 抛 ApiError
```

### started 之后

```text
HTTP 已经是 200
→ started
→ delta
→ error
```

这时 `error` 是一条正常收到的 SSE 业务终点，里面携带数据库中已经标记为 FAILED 的助手消息。它不是第二个 HTTP 响应。

前端因此必须先处理 HTTP 状态，再消费 SSE 业务事件。

## 7. 网络 chunk 不等于一条 SSE 事件

浏览器读取到的字节块可能是：

```text
chunk 1: event: delta\ndata: {"delta":"会
chunk 2: 议"}\n\nevent: completed\ndata:
chunk 3: {...}\n\n
```

原因是 TCP、代理和浏览器可以任意决定分块边界。不能假设一次 `reader.read()` 就得到一条完整事件。

`parseSseStream` 使用缓冲区：

```text
读取 Uint8Array
→ TextDecoder 流式解码 UTF-8
→ 追加到字符串 buffer
→ 查找空行分隔符
→ 每找到一个完整帧就解析
→ 剩余半帧保留到下一个 chunk
```

`TextDecoder.decode(value, { stream: true })` 也很重要，因为一个中文 UTF-8 字符本身可能被拆到两个网络块中。

如果解析出现协议错误或上层提前结束迭代，解析器会调用 `reader.cancel()`，通知 fetch 释放底层响应流；正常读到 EOF 时只释放 reader 锁。

## 8. SSE 帧解析与业务事件校验为什么分两层

通用 SSE 解析器只认识：

```typescript
{
  event: string;
  data: unknown;
}
```

会话流模块再把 `unknown` 校验成：

```text
started
delta
completed
error
```

校验内容包括：

- `event:` 必须与 JSON `type` 一致。
- 第一项必须是 `started`。
- `started` 不能重复。
- `delta` 必须包含消息 ID 和非空正文。
- `completed/error` 必须包含完整消息结构。
- 终点后不能再出现事件。
- 流结束前必须出现 `completed` 或 `error`。

通用解析器以后还可以复用到 Agent Run 事件，而不用包含聊天业务判断。

## 9. Pinia 中保存什么

认证 Store 当前保存：

- 短期 Access Token。
- 当前用户公开信息。
- 是否正在加载。

令牌写入 `sessionStorage`：

- 刷新当前标签页可以恢复。
- 关闭标签页后自动清除。
- 不保存邮箱密码。

`sessionStorage` 仍然能被同源 JavaScript 读取，所以不能防御 XSS。作品第一版使用短期 JWT 和严格前端依赖；更高安全级别可以改成后端设置的 HttpOnly Cookie，并额外处理 CSRF。

## 10. 为什么开发环境使用 Vite 代理

浏览器请求保持：

```text
/api/v1/...
```

开发服务器把 `/api` 转发到：

```text
http://127.0.0.1:8000
```

这样浏览器看到的仍是同源地址，本地开发不必先给 FastAPI 加宽泛的 CORS 配置。生产环境以后由 Nginx 让前端和 `/api` 保持同域。

如果确实采用前后端不同域名，可以在 `.env` 设置：

```text
VITE_API_BASE_URL=https://api.example.com
```

同时后端必须配置准确的 CORS 白名单，不能直接允许所有来源和认证信息。

## 11. 测试保护什么

- SSE 事件被拆到多个网络 chunk 后仍能顺序恢复。
- CRLF 和 LF 都能识别。
- 中文字符跨 chunk 不会乱码。
- 非法 JSON 抛出稳定协议错误。
- 未闭合 SSE 帧不能静默丢失。
- SSE 协议错误会主动取消底层响应流。
- POST 流式请求携带 JSON 与 Bearer Token。
- JSON 404 预检错误转换为 `ApiError`。
- 消息流缺少终点时报告协议错误。
- `event` 与 `data.type` 不一致时拒绝。
- 普通网络故障和主动 Abort 能被区分。
- 登录成功后 Pinia 与 `sessionStorage` 同步。
- 退出登录清理令牌和用户状态。

测试最开始因为生产模块不存在而出现 4 个失败套件；实现后变成 4 个套件、14 条测试全部通过。

## 12. 浏览器验收

除了单元测试和构建，本模块还启动了真实 Vite 开发服务器并在浏览器检查：

- 页面标题正确。
- Vue 根组件成功挂载。
- 中文文字正常渲染。
- `Vue → Pinia → Fetch → FastAPI SSE` 调用链可见。
- 控制台没有 warning 或 error。

这只能证明当前底座页面正常渲染。登录和流式聊天的浏览器端到端测试要等对应页面完成后再增加。

## 13. 常用命令

在前端目录运行：

```powershell
cd frontend
npm install
npm run dev
npm run test:run
npm run type-check
npm run build
```

- `dev`：启动本地开发服务器和热更新。
- `test:run`：单次运行全部 Vitest 测试，适合 CI。
- `type-check`：检查 TypeScript 和 Vue SFC 类型。
- `build`：先类型检查，再生成 `dist` 生产文件。

## 14. 本模块没有数据库迁移

本模块只新增前端工程、请求协议和浏览器状态，没有修改 PostgreSQL 表结构，因此不需要 Alembic migration。

上一模块的 `CANCELLED` 数据库枚举迁移仍需在数据库环境执行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## 15. 当前还没有实现什么

- 注册表单页面。
- 会话列表、创建、归档和删除界面。
- 聊天消息气泡。
- delta 逐段追加显示。
- `AbortController` 停止按钮。
- 重试期间按钮禁用。
- 前端组件测试和完整端到端测试。

本模块的页面只是用于验证 Vue 能正常挂载的底座页，不是最终产品界面。

登录页面、登录状态恢复与 Vue Router 守卫已在[第 27 模块](27-vue-login-and-route-guard.md)完成。

## 16. 常见错误

- 在 Vue 组件里直接按 `reader.read()` 次数切消息。
- 普通 JSON 请求和流式请求各自重复认证与错误处理。
- 把 HTTP 404 和流内 `error` 当成相同协议。
- 不检查 SSE 终点，半截回答被误认为完成。
- SSE 解析失败后只释放 reader 锁，却没有取消底层响应流。
- 把 `AbortError` 显示成网络故障。
- 只写 TypeScript 类型，不校验网络传入的 unknown JSON。
- 把邮箱密码写入浏览器存储。
- 盲目升级到最新 TypeScript，却不运行 `vue-tsc` 验证工具链兼容性。
- 只执行 Vite build，不单独运行测试和浏览器渲染检查。

## 17. 面试表达

> 我把前端流式协议拆成通用 SSE 字节解析和聊天业务事件校验两层。解析器使用 TextDecoder 的 streaming 模式与字符串缓冲区处理任意 TCP 分块；业务层再验证 started、delta 和终点顺序。普通请求和流式请求复用 fetch 客户端统一添加 Bearer Token，并区分 HTTP 预检错误、SSE error、网络失败和用户 Abort。Pinia 只保存短期 JWT 与公开用户信息，组件不直接承担协议解析。

## 18. 小练习

请解释：

```text
reader.read() 返回一次
```

为什么不一定等于：

```text
收到一条 delta 事件
```

答案要点：浏览器读取的是任意大小的网络字节块，一条事件可能跨多个块，多个事件也可能合并在一个块中，所以必须依靠 SSE 的空行分隔符组装帧。
