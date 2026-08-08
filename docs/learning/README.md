# 学习笔记

本目录记录每个已完成模块的设计、代码调用链、测试、取舍、面试表达和练习。

## 模块

1. [仓库工程基础](01-repository-foundation.md)
2. [Redis 短期记忆接入](02-memory-dependency-injection.md)
3. [异步数据库与缓存降级](03-async-database-and-cache-fallback.md)
4. [消息状态与模型失败处理](04-message-status-and-model-failure.md)
5. [中文代码注释与阅读约定](05-code-commenting-conventions.md)
6. [失败消息原位重试](06-retry-failed-message.md)
7. [异步 LLM Gateway 基础](07-async-llm-gateway.md)
8. [用户持久化基础](08-user-persistence-foundation.md)
9. [密码安全与用户注册](09-password-security-and-registration.md)
10. [登录、JWT 与当前用户](10-login-jwt-and-current-user.md)
11. [会话所有权与完整 CRUD](11-conversation-ownership-and-crud.md)
12. [Token 预算与完整轮次裁剪](12-token-budget-and-complete-turns.md)
13. [会话摘要表与覆盖游标](13-conversation-summary-persistence.md)
14. [摘要与最近完整轮次的上下文构建](14-summary-and-recent-context.md)
15. [持久化摘要任务与 Celery 基础](15-persistent-summary-job-and-celery.md)
16. [摘要任务触发与提交后可靠投递](16-summary-job-scheduling-and-dispatch.md)
17. [摘要 Worker 原子领取与重复执行保护](17-summary-worker-atomic-claim.md)
18. [滚动摘要生成、冻结范围与原子保存](18-rolling-summary-generation.md)
19. [摘要批次 Token 上限与调用前预算检查](19-summary-batch-token-budget.md)
20. [摘要失败分类、延迟重试与指数退避](20-summary-failure-retry-backoff.md)
21. [摘要任务周期恢复与投递租约](21-summary-recovery-and-dispatch-lease.md)
22. [模型流式输出的内部契约](22-internal-llm-stream-contract.md)
23. [Service 流式消息状态与事务边界](23-service-stream-message-state.md)
24. [FastAPI SSE 流式协议层](24-fastapi-sse-transport.md)
25. [流式取消、断线终态与原子状态转换](25-stream-cancellation-state.md)
