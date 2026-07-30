# 模块 11：会话所有权与完整 CRUD

## 要解决的问题

JWT 只能证明请求者是谁，不能自动保证数据隔离。如果会话查询只使用 `conversation_id`，用户拿到
另一个会话 UUID 后仍可能读取消息、发送问题或重试失败消息。

本模块把用户所有权加入所有会话访问路径，并移除原来的固定用户 UUID。

## 接口

```text
POST   /api/v1/conversations
GET    /api/v1/conversations
GET    /api/v1/conversations/{conversation_id}
PATCH  /api/v1/conversations/{conversation_id}
DELETE /api/v1/conversations/{conversation_id}

POST   /api/v1/conversations/{conversation_id}/messages
GET    /api/v1/conversations/{conversation_id}/messages
POST   /api/v1/conversations/{conversation_id}/messages/{message_id}/retry
```

所有接口都依赖 `CurrentUserDep`，必须携带有效 Bearer Token。

## 所有权调用链

```text
Authorization: Bearer <token>
→ CurrentUserDep 得到 current_user
→ Router 只传 current_user.id
→ ConversationService
→ ConversationRepository.get_by_id_for_user
→ WHERE conversations.id = ? AND conversations.user_id = ?
```

Repository 的所有权条件是最终应用查询边界。仅在 Router 判断用户 ID 不够安全，因为 Service 可能
被其他路由、后台任务或测试直接调用。

## 为什么跨用户访问返回 404

不存在的会话和属于其他用户的会话统一返回：

```text
404 CONVERSATION_NOT_FOUND
```

如果跨用户访问返回 403，而真正不存在返回 404，攻击者可以批量判断哪些 UUID 确实存在。统一 404
减少了资源枚举信息。

## 数据库外键与历史数据

`conversations.user_id` 现在通过外键关联 `users.id`，数据库层不允许写入不存在的所有者。

旧版本可能已经保存固定 UUID 会话。迁移不能直接添加外键，否则历史孤立数据会导致升级失败。因此
迁移先为每个孤立 `user_id` 创建一个停用的占位用户，再添加外键：

```text
扫描历史 conversation.user_id
→ 创建 legacy-<uuid>@local.invalid 停用用户
→ 添加 conversations.user_id → users.id 外键
```

占位用户使用不可登录密码标记且 `is_active=false`，只用于保留历史数据完整性。

## CRUD 规则

- 列表默认隐藏归档会话，可用 `include_archived=true` 查看全部。
- `PATCH` 只允许修改标题和归档状态。
- 空 PATCH 或显式传入 `null` 会在 Pydantic 层被拒绝。
- 删除会话由数据库外键级联删除消息，ORM 使用 `passive_deletes` 避免先加载全部消息。
- 用户删除采用外键 `RESTRICT`，避免误操作级联清空全部企业会话。

## PostgreSQL 与 Redis 删除顺序

```text
删除 PostgreSQL 会话和消息
→ commit
→ 删除 Redis 短期记忆
```

PostgreSQL 是业务事实来源。数据库提交成功后，Redis 删除失败只能记录降级日志，不能恢复已删除
会话。缓存键仍有 24 小时 TTL，且后续所有数据库所有权查询都会返回 404，不会把残留缓存暴露给
用户。

## 测试覆盖

- 会话所有者字段具有数据库外键。
- Repository 查询同时包含会话和用户条件。
- 跨用户访问与不存在资源统一返回 404。
- 列表只查询当前用户数据。
- 更新和删除由 Service 提交事务。
- 删除数据库成功后清理 Redis，缓存故障不回滚数据库。
- Router 使用当前登录用户，不再出现固定 UUID。
- 全部会话接口位于 `/api/v1` 且声明 Bearer 安全要求。

## 面试表达

“认证和授权是两层：JWT 恢复当前用户，Repository 查询再强制拼接 user_id。所有会话、消息和重试
路径都先执行同一个所有权查询；跨用户与不存在统一返回 404。数据库还用外键保证所有者真实存在。”

## 小练习

解释为什么下面只在 Router 中判断的方案仍不够安全：

```python
if conversation.user_id != current_user.id:
    raise ForbiddenError()
```

答案要点：其他入口可能绕过 Router 直接调用 Service；而且先按 ID 查询再判断会把其他用户实体加载
进内存。更稳妥的方式是在 Repository SQL 中同时过滤 `conversation_id` 和 `user_id`，让数据库只
返回当前用户有权访问的记录。
