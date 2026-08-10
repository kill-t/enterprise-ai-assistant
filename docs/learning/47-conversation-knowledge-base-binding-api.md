# 会话知识库绑定 API 与原子替换

## 1. 本模块解决什么问题

上一模块只建立了 `conversation_knowledge_bases` 关联表，还没有让用户通过接口选择知识库。
本模块新增两个接口：

```text
GET /api/v1/conversations/{conversation_id}/knowledge-bases
PUT /api/v1/conversations/{conversation_id}/knowledge-bases
```

`GET` 查询会话当前选择；`PUT` 使用下面的 JSON 把当前选择完整替换成新集合：

```json
{
  "knowledge_base_ids": [
    "276c947e-7ed1-4214-81d9-167a2e7010a8"
  ]
}
```

传入空数组表示解绑全部知识库。第一版每个会话最多选择 5 个知识库，并拒绝重复 ID。

## 2. 请求怎样经过各层

```text
Vue（后续实现）
→ FastAPI Router 解析路径、JSON 和当前用户
→ ConversationKnowledgeBaseService 校验业务规则并控制事务
→ ConversationRepository 验证会话所有权
→ KnowledgeBaseRepository 批量锁定并验证知识库
→ ConversationKnowledgeBaseRepository 删除旧绑定、写入新绑定
→ Service commit
→ Router 返回统一 ApiResponse
```

各层职责不同：

- Schema 只负责请求和响应形状、数量及重复值校验。
- Router 只处理 HTTP，不直接编写 SQL。
- Service 负责所有权、归档状态、操作顺序和事务。
- Repository 只负责查询或修改数据库，不自行 `commit`。

绑定逻辑单独放在 `ConversationKnowledgeBaseService`，避免把聊天、会话 CRUD 和 RAG 选择
全部堆进原有 `ConversationService`。

## 3. 为什么使用 PUT 完整替换

用户在界面上看到的是一组复选框，点击保存时最自然的含义是：

```text
数据库最终选择 = 本次提交的全部 ID
```

这正是 `PUT` 的替换语义。例如旧集合是 `[A, B]`，请求提交 `[B, C]`，结果就是 `[B, C]`，
而不是继续追加成 `[A, B, C]`。

如果分别提供“增加一个”和“删除一个”接口，前端需要发多次请求，中途可能只成功一部分。
完整替换更容易让用户看到的状态与数据库最终状态一致。重复提交相同请求，最终结果也相同。

## 4. 为什么要先完成全部校验再写数据库

正确顺序是：

```text
验证会话属于当前用户
→ 一次查询目标知识库并校验所有权
→ 检查请求中的每个 ID 都存在
→ 检查没有知识库被归档
→ 删除旧绑定并写入新绑定
→ commit
```

假如在验证每个知识库时就逐条插入，遇到最后一个无效 ID 时，前面的绑定可能已经写入，
会形成“只保存了一半”的结果。现在所有验证都发生在写操作之前；验证失败时旧绑定完全不变。

不存在和跨用户资源统一返回 404。这样攻击者不能根据 403 和 404 的差别猜出另一个用户是否
拥有某个会话或知识库。

## 5. 原子替换和事务回滚

Repository 的 `replace` 做两件事：

```text
DELETE 当前会话的全部旧绑定
INSERT 新绑定
```

这两条 SQL 使用同一个 `AsyncSession` 和同一个数据库事务。只有全部成功后 Service 才调用
`commit`。如果插入或刷新失败，Service 调用 `rollback`，PostgreSQL 会撤销本次删除，旧绑定
仍然存在。

所以这里的“原子”表示：

```text
要么删除与插入全部成功，要么数据库状态完全不变。
```

事务由 Service 控制，因为一次业务操作可能组合多个 Repository；单个 Repository 不知道完整
业务边界在哪里。

## 6. 为什么批量查询还要 SELECT FOR UPDATE

普通批量查询只能确认“查询那一刻知识库未归档”。并发情况下可能发生：

```text
请求 A：查询到知识库未归档
请求 B：归档同一个知识库并提交
请求 A：继续创建绑定
```

绑定请求通过 `SELECT ... FOR UPDATE` 锁定选中的知识库行，直到当前事务提交或回滚。归档请求
若也按正确事务规则修改同一行，就必须等待，避免检查结果在写入前被并发改变。

锁只用于 `PUT` 的写流程；普通 `GET` 不需要加锁。锁不能代替业务校验，它只是保护“校验到
提交”这段时间内的数据稳定性。

## 7. 为什么 GET 仍返回后来被归档的绑定

新绑定时不允许选择已归档知识库，但一个已绑定知识库可能后来才被归档。`GET` 不会偷偷删除
它，也不会把它隐藏，而是返回 `is_archived=true`，让前端展示并允许用户移除。

如果查询时直接过滤掉归档项，前端会误以为它已解绑，而数据库中实际仍保留关联。这会让界面
状态和事实状态不一致。

后续聊天检索会只使用可检索的有效知识库；管理接口则保留完整可见性。

## 8. 为什么 SQL 还要重复过滤所有权

Service 已验证当前用户，但绑定查询的 SQL 仍同时包含：

```text
conversations.user_id = 当前用户
knowledge_bases.user_id = 当前用户
```

这是纵深防御。即使历史脏数据、维护脚本或未来代码错误地创建了跨用户关联，读取接口也不会把
另一用户的知识库返回出来。

不过 SQL 过滤不能替代 Service 的明确错误处理：Service 仍负责把“不存在”和“跨用户”统一转换
成稳定的业务 404，并确保写入前目标集合完整。

## 9. 请求校验规则

### 最多 5 个知识库

`Field(max_length=5)` 在进入 Service 前拒绝过长列表，防止一次会话把大量知识库全文都送入模型，
也为后续检索延迟和 Prompt Token 预算建立第一层边界。

### 不能重复

Pydantic `model_validator` 比较列表长度和集合长度：

```text
len(ids) != len(set(ids)) → 存在重复值 → 返回 422
```

数据库复合主键也会阻止重复绑定，但请求层提前返回清楚的 422，用户不必看到数据库唯一约束
异常。数据库约束仍然是最后一道保护。

### 空数组合法

空数组明确表示“解绑全部”，所以它不会被当成参数缺失。此时不查询知识库，直接在事务中删除
现有绑定。

## 10. 正常、失败和并发路径

| 场景 | 结果 | 数据库变化 |
| --- | --- | --- |
| 合法 ID | 返回 200 和新选择 | 原子替换并提交 |
| 空数组 | 返回 200 和空列表 | 删除全部绑定并提交 |
| 重复或超过 5 个 | 返回 422 | Service 尚未执行，无变化 |
| 会话不存在或跨用户 | 返回 404 | 无变化 |
| 任一知识库不存在或跨用户 | 返回 404 | 无变化 |
| 任一知识库已归档 | 返回 409 | 无变化 |
| 删除后插入失败 | 异常交给统一错误处理 | rollback，旧绑定恢复 |

## 11. 测试分别保护什么

`backend/tests/test_conversation_knowledge_base_binding.py` 覆盖：

- Schema 的数量上限和重复 ID。
- Repository 查询同时过滤会话与知识库所有者。
- Repository 只 `flush`，不越权提交事务。
- 会话不存在、知识库缺失、跨用户和已归档错误。
- Service 完整验证后才调用替换，并保持客户端提交顺序。
- 空列表解绑。
- 成功提交与失败回滚。
- HTTP 接口需要 JWT，并出现在 OpenAPI 契约中。

全量回归结果：

```text
294 passed
Ruff: All checks passed!
```

真实 PostgreSQL 和 HTTP 联调执行了：

```text
绑定一个知识库 → GET 查到绑定 → PUT [] 清空 → 再次绑定
```

四次请求均返回 200，最后直接查询关联表得到 1 条绑定，且会话用户与知识库用户相同。

## 12. 关键代码入口

- 请求与响应契约：
  `backend/app/modules/conversation/schema/conversation_knowledge_base_schema.py`
- 绑定 SQL：
  `backend/app/modules/conversation/repository/conversation_knowledge_base_repository.py`
- 批量知识库查询：
  `backend/app/modules/knowledge/repository/knowledge_base_repository.py`
- 业务校验与事务：
  `backend/app/modules/conversation/service/conversation_knowledge_base_service.py`
- FastAPI 依赖：
  `backend/app/modules/conversation/dependencies.py`
- HTTP 路由：
  `backend/app/modules/conversation/api/conversation_router.py`
- 测试：
  `backend/tests/test_conversation_knowledge_base_binding.py`

## 13. 本模块尚未做什么

- 还没有根据一个会话的多个绑定知识库聚合检索结果。
- 还没有把证据加入 LLM Prompt。
- 还没有在助手消息事务中保存 `message_citations`。
- 还没有通过 SSE 发送 `sources` 事件。
- 前端还没有知识库选择组件。

下一小步应先实现“多知识库检索聚合”的内部 Service，让聊天层拿到统一、排序后的证据；仍不急着
同时修改 LLM、SSE 和前端。

## 14. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 无效 ID 后旧绑定被清空 | 在完成全部校验前执行了 DELETE |
| 插入失败后绑定消失 | DELETE 和 INSERT 不在同一事务，或异常时没有 rollback |
| 已归档知识库在管理页消失 | GET 错误过滤了归档项，应返回状态让用户移除 |
| 绑定顺序和请求不同 | 数据库 `IN` 查询不保证顺序，需要按请求 ID 重新组装响应 |
| 跨用户知识库被返回 | SQL 只过滤了会话所有者，没有同时过滤知识库所有者 |
| 重复 ID 变成 500 | 只依赖数据库主键，应先在 Schema 返回 422 |
| 并发归档后仍创建绑定 | 校验查询没有锁定知识库行 |

## 15. 面试表达

> 我为会话知识库选择提供了 GET 和 PUT 接口。PUT 表示完整替换，先按当前用户批量查询并锁定
> 目标知识库，确认集合完整且没有归档项后，才在同一事务中删除旧绑定、写入新绑定。Repository
> 只执行 SQL 和 flush，Service 统一 commit 或 rollback，因此失败不会留下半套选择。读取 SQL 同时
> 过滤会话和知识库所有者，并保留已归档绑定供前端展示和移除。请求层限制最多 5 个且拒绝重复
> ID，数据库复合主键作为最后一道一致性保护。

## 16. 小练习（约 15–20 分钟）

在 `backend/tests/test_conversation_knowledge_base_binding.py` 中增加：

```text
test_get_selection_keeps_archived_binding_visible
```

练习目标：证明一个后来归档的已绑定知识库仍会出现在 `GET` 结果里，并带有
`is_archived=True`。

建议步骤：

1. 参考已有 `get_selection` 测试创建 Service 和一个 `is_archived=True` 的知识库实体。
2. 让会话 Repository 返回当前用户的会话。
3. 让绑定 Repository 返回这个已归档实体。
4. 调用 `get_selection`。
5. 断言响应中仍有一个知识库，且 `is_archived is True`。
6. 断言没有调用 `replace` 和 `commit`，因为 GET 不应修改状态。
7. 运行：
   `uv run pytest tests/test_conversation_knowledge_base_binding.py -q`

答案要点：管理查询需要忠实展示数据库的现有绑定；“不能新绑定归档知识库”是 PUT 的规则，
不能错误地变成 GET 的隐藏规则。
