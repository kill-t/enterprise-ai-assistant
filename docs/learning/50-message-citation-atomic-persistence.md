# 消息引用快照与原子持久化

## 1. 本模块解决什么问题

上一模块已经把检索来源按 `[S1]`、`[S2]` 放进 Grounded Prompt，但这些来源只存在于本次模型
请求的内存里。请求结束后，数据库只有助手正文，页面刷新、文档重新入库或后续审计时都无法回答：

```text
这条答案当时依据了哪份文件、哪一页、哪段原文？
```

本模块把模型实际看到的最终来源保存到 `message_citations`，并保证助手正文和引用只能一起成功：

```text
模型成功返回
→ 更新助手消息正文和 Token
→ 删除该助手消息的旧引用
→ 按 S1、S2 顺序写入本次引用快照
→ 同一个 PostgreSQL 事务提交
→ 提交成功后才回填 Redis、调度摘要
```

本模块不创建新表，所以不需要 Alembic 迁移。`message_citations` 已在 RAG 数据地基阶段建立。

## 2. 为什么助手消息和引用必须在同一个事务

如果分两次提交，可能出现两种半套状态：

```text
先提交回答，引用写入失败
→ 用户看到了答案，但系统无法证明答案来自哪里

先提交引用，回答写入失败
→ 数据库有来源，却没有可以展示的答案
```

这两个写入共同描述一次回答完成事件，因此必须满足原子性：

```text
回答 + 引用：要么全部提交，要么全部回滚
```

`MessageCitationRepository` 只执行 `DELETE`、`INSERT` 和 `flush`，不调用 `commit`。事务边界由
`ConversationService` 统一控制，这样 Repository 不会在 Service 尚未完成全部业务写入时提前提交。

## 3. 为什么同时保存活外键和不可变快照

每条引用包含两类数据。

### 活外键

```text
knowledge_base_id
document_id
chunk_id
```

它们用于关联当前仍存在的知识库、文档和 Chunk。来源被删除时，数据库使用 `SET NULL`，历史回答
不会因为外键级联而一起消失。

### 回答当时的快照

```text
knowledge_base_name_snapshot
filename_snapshot
chunk_index_snapshot
content_snapshot
heading_path_snapshot
page_start / page_end
retrieval_score
source_order
```

快照回答的是“生成这条答案时模型看到了什么”。即使文件后来改名、Chunk 重新切分或原文被删除，
旧回答仍然可以展示当时的文件名、页码和原文片段。

可以这样理解：

```text
外键 = 能否跳到现在的来源
快照 = 能否还原过去的证据
```

## 4. 为什么保存最终来源，而不是全部候选

向量检索可能先召回很多候选，但只有阈值过滤、跨知识库排序和 Top-K 截断后的来源才进入 Prompt。
引用表必须保存模型真正看到的这一组来源，而不是检索过程中的全部候选。

本次数据流为：

```text
ConversationKnowledgeSearchService
→ MultiKnowledgeSearchResponse.results
→ _AssistantResponsePlan.sources
→ _MessageGenerationContext.sources
→ Chat Provider 看见相同来源
→ _complete_assistant_response 保存相同来源
```

因此 Prompt 中的 `[S1]` 与引用表的 `source_order = 1` 对应，`[S2]` 与 `source_order = 2` 对应。

当前实现证明“这些来源被发送给模型”，暂时还不证明模型正文一定正确使用了每个编号。合法引用编号
校验、引用覆盖率和引用忠实度属于后续评测能力。

## 5. 为什么重试采用整组替换

失败消息重试会根据当前知识库绑定、当前文档状态和当前阈值重新检索。新结果可能与旧结果完全不同：

```text
上次：[S1=A, S2=B]
本次：[S1=C]
```

如果只追加，就会错误保留 A、B。因此 `replace_for_message` 先删除原消息的所有引用，再写入本次
完整来源。删除和新增仍在同一事务里；任何一步失败，旧引用不会被半删半改。

普通聊天和无证据拒答也会传入空元组：

```text
sources = ()
```

空来源仍执行删除，确保失败消息重试后改走普通聊天或拒答时，不会残留上一次的引用。

## 6. 完成事务失败后为什么还需要补偿

用户消息和 `PENDING` 助手占位在调用模型前已经提交。模型成功后，如果回答或引用的完成事务失败，
本次事务回滚只能撤销 `COMPLETED` 修改，数据库中的原占位仍是 `PENDING`。

若不处理，前端会一直显示“生成中”，但后台已经停止。因此失败路径为：

```text
回答或引用 flush / commit 失败
→ rollback 半套完成事务
→ 条件 UPDATE：只有仍为 PENDING 才改为 FAILED
→ 提交补偿事务
→ 返回 CITATION_PERSISTENCE_FAILED
```

条件更新非常重要。若用户取消、另一请求或并发操作已经把消息改成其他终态，补偿逻辑不能覆盖它：

```sql
UPDATE messages
SET status = 'FAILED', ...
WHERE id = :message_id
  AND status = 'PENDING'
```

补偿成功后用户可以使用已有的原位重试能力。补偿本身也失败时，服务记录中文异常日志并再次尽力
回滚；未来可通过监控和恢复扫描处理这种数据库持续不可用场景。

## 7. 提交后才执行哪些操作

Redis 回填和会话摘要调度都位于数据库提交之后：

```text
PostgreSQL 回答与引用提交成功
→ Redis 保存完成消息
→ 尽力创建摘要任务
```

如果引用保存失败：

- 不把这条未完成回答写入 Redis。
- 不把它加入后续模型上下文。
- 不调度摘要任务去总结一条数据库中不存在的完成回答。

PostgreSQL 仍然是事实来源，Redis 只是可重建的短期缓存。

## 8. 关键代码入口

- 引用整组替换与快照映射：
  `backend/app/modules/conversation/repository/message_citation_repository.py`
- 助手回答、引用事务和失败补偿：
  `backend/app/modules/conversation/service/conversation_service.py`
- `PENDING → FAILED` 条件更新：
  `backend/app/modules/conversation/repository/message_repository.py`
- 引用 ORM 与约束：
  `backend/app/modules/conversation/entity/message_citation.py`
- 原子性与快照测试：
  `backend/tests/test_message_citation_persistence.py`
- Grounded 来源传递回归：
  `backend/tests/test_grounded_chat.py`

## 9. 测试保护了什么

新增测试覆盖：

- 删除旧引用后按 `S1`、`S2` 顺序映射完整快照。
- Repository 只 `flush`，不会擅自 `commit`。
- 空来源也会清除旧引用。
- 补偿 SQL 只更新仍为 `PENDING` 的消息。
- 回答和引用依次 flush 后只提交一次。
- 引用失败时先回滚，再把占位补偿为 `FAILED`。
- 失败时不写 Redis、不调度摘要。
- Grounded Chat 保存检索到的准确来源。
- 普通聊天和无证据拒答保存空来源。
- 非流式、失败重试和原有流式聊天没有回归。

自动测试结果：

```text
314 passed
Ruff: All checks passed!
```

真实 PostgreSQL 验收使用已有会话和 Chunk，临时创建一条回答和一条引用，验证了外键、快照和 S1
顺序。最后整体回滚，验收前后均为：

```text
messages = 10
message_citations = 0
```

第一次验收脚本只导入部分 ORM 实体，SQLAlchemy 在任何写入前报告找不到
`DocumentIngestionJobEntity`。改为先导入会话和知识模块的统一 `models` 注册入口后通过。这说明独立
脚本使用 ORM 时也必须注册完整关系映射。

## 10. 正常与失败路径总结

| 场景 | 引用 | 消息终态 |
| --- | --- | --- |
| 有可靠 RAG 证据 | 保存最终 S1、S2 快照 | `COMPLETED` |
| 无活动知识库的普通聊天 | 空引用 | `COMPLETED` |
| 有活动知识库但无可靠证据 | 空引用 | `COMPLETED` 拒答 |
| Chat Provider 失败 | 不进入完成事务 | 原有逻辑保存 `FAILED` |
| 引用或回答完成事务失败 | 整体回滚 | 尽力补偿为 `FAILED` |
| 重试得到新来源 | 删除旧引用后写入新组 | 复用原助手消息 ID |

## 11. 本模块尚未做什么

- 历史消息 API 还没有读取并返回引用。
- SSE 还没有发送 `sources` 事件。
- 流式聊天还没有接入 RAG 检索和引用。
- 前端还没有显示引用卡片。
- 还没有检查模型正文中的 `[Sx]` 是否越界或缺失。
- 还没有建立引用有效率、忠实度等评测指标。

下一小步是 **RAG-5F：历史消息引用读取协议**。它会批量读取引用，扩展消息响应结构，使页面刷新后
仍能拿到文件名、页码和原文快照；仍先不改 SSE 和前端展示。

## 12. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 页面刷新后引用消失 | 只在内存或 SSE 中发送，没有持久化快照 |
| 有答案但引用为空 | 回答与引用分两次提交，第二次失败 |
| 重试后同时出现新旧来源 | 使用追加写入，应整组替换 |
| 普通聊天残留旧引用 | 空来源时跳过了删除 |
| 文档删除后旧引用卡片也消失 | 只保存外键，没有保存不可变快照 |
| 引用失败后页面一直生成中 | 回滚后没有补偿原 `PENDING` 占位 |
| Repository 内部调用 commit | 事务边界下沉，Service 无法保证多表原子性 |
| 真实库脚本提示 ORM 类找不到 | 没有导入模块统一 `models` 注册入口 |

## 13. 面试表达

> 我将模型实际看到的最终 RAG 来源保存为消息引用，而不是保存所有召回候选。引用同时保留当前
> 知识库、文档、Chunk 外键和生成时的文件名、页码、原文快照，来源删除后仍可审计。助手正文与
> 引用由 Service 在同一 PostgreSQL 事务中提交，Repository 只 flush。失败消息重试采用整组替换，
> 空来源也会清除旧引用。若完成事务失败，先整体回滚，再用条件 UPDATE 把仍为 PENDING 的占位
> 补偿为 FAILED，避免页面永久停在生成中；Redis 和摘要调度只在数据库提交成功后执行。

## 14. 小练习（约 15–20 分钟）

在 `backend/tests/test_message_citation_persistence.py` 中增加测试：

```text
test_three_sources_keep_global_source_order
```

练习目标：证明三条来源始终映射为 `source_order = [1, 2, 3]`，且每条引用的原文快照与输入来源
一一对应。

建议步骤：

1. 使用 `build_source` 创建三条不同正文的来源。
2. 调用 `MessageCitationRepository.replace_for_message`。
3. 断言返回实体顺序是 `[1, 2, 3]`。
4. 断言三个 `content_snapshot` 与输入正文顺序相同。
5. 断言 `db.commit` 没有被调用。
6. 运行：`uv run pytest tests/test_message_citation_persistence.py -q`。

答案要点：编号必须在最终来源列表上使用 `enumerate(..., start=1)` 生成；不能依赖数据库主键或创建
时间，因为 UUID 和并发写入顺序都不能表达 Prompt 中的 S1、S2、S3。
