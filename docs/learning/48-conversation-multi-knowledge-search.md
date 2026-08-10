# 会话多知识库检索聚合

## 1. 本模块解决什么问题

前两个模块已经完成：

```text
单个知识库向量检索
会话与多个知识库的持久化绑定
```

但二者还没有连接。一个会话即使绑定了项目规范库和会议资料库，聊天层仍然不知道应该怎样把
多个知识库交给检索系统。

本模块建立下面的内部调用链：

```text
user_id + conversation_id + query
→ 验证会话属于当前用户
→ 读取会话的全部知识库绑定
→ 跳过后来被归档的绑定
→ 校验活动知识库使用相同 Embedding 配置
→ 生成一次查询向量
→ 使用一条 pgvector SQL 跨库召回候选
→ 相似度过滤、正文去重、全局排序和 Top-K
→ 返回带知识库、文件、页码和 Chunk 的统一证据
```

本模块是内部能力，还没有新增 HTTP 接口，也没有调用聊天大模型。

## 2. 它在整体架构中的位置

```text
Conversation Chat Service（下一模块接入）
→ ConversationKnowledgeSearchService
   ├─ ConversationRepository
   ├─ ConversationKnowledgeBaseRepository
   └─ KnowledgeSearchService.search_many
      ├─ Embedding Provider
      └─ KnowledgeSearchRepository
         ↓
       PostgreSQL / pgvector
```

`ConversationKnowledgeSearchService` 是很薄的会话适配层，只理解会话所有权和知识库绑定。
真正的向量检索、阈值、去重与排序仍在知识模块，因此以后 Knowledge Agent 可以直接复用核心
`KnowledgeSearchService`，不用复制聊天代码。

## 3. 数据怎样变化

输入问题：

```text
“项目会议应该准备哪些资料？”
```

首先变成结构化请求：

```text
KnowledgeSearchRequest
├─ query = “项目会议应该准备哪些资料？”
└─ top_k = 5
```

Embedding Provider 将问题转换为一个 1024 维查询向量：

```text
[0.012, -0.038, ..., 0.027]
```

Repository 使用同一个向量搜索多个知识库 ID，返回内部候选：

```text
MultiKnowledgeSearchCandidate
├─ knowledge_base_id / knowledge_base_name
├─ document_id / filename
├─ chunk_id / chunk_index / content_hash
├─ content / heading_path / page_start / page_end
└─ score
```

Service 过滤和排序后返回：

```text
ConversationKnowledgeSearchResponse
├─ conversation_id
├─ query
├─ searched_knowledge_base_ids
├─ skipped_archived_knowledge_base_ids
├─ similarity_threshold
└─ results[]
```

后续聊天模块会把 `results` 转换为 `[S1]`、`[S2]` 证据，并保存到引用表。

## 4. 为什么不同 Embedding 模型不能直接混排

假设两个知识库分别使用模型 A 和模型 B：

```text
模型 A 检索结果：0.75
模型 B 检索结果：0.72
```

不能仅凭数字断定模型 A 的结果更相关。两个模型的训练方式、向量空间和分数分布不同，`0.75`
和 `0.72` 没有统一标尺。

更严重的是，模型 A 生成的查询向量不能拿去搜索模型 B 生成的文档向量。即使向量维度都是
1024，它们也不是同一个坐标空间。

因此第一版要求活动知识库的以下快照完全相同：

```text
embedding_provider
embedding_model
embedding_dimension
```

不一致时在产生模型调用费用前返回：

```text
409 KNOWLEDGE_SEARCH_CONFIG_MISMATCH
```

替代方案是按模型分组检索后增加统一的 Cross-Encoder 重排序器，但会增加模型、延迟、成本和评测
复杂度。第一版先保持结果可解释；RAG-6 有评测集后再决定是否值得引入。

## 5. 为什么不是循环搜索每个知识库

一种直观写法是：

```text
for knowledge_base in knowledge_bases:
    分别生成向量
    分别执行 SQL
    最后在 Python 合并
```

如果 5 个知识库使用相同配置，这样会重复生成 5 次完全相同的查询向量，并执行 5 次数据库往返。

当前方案把知识库 ID 作为 SQL 的 `IN` 条件：

```sql
WHERE knowledge_bases.id IN (...)
ORDER BY document_chunks.embedding <=> :query_vector
LIMIT :candidate_limit
```

因此同配置情况下只需要：

```text
1 次 Embedding 调用
+ 1 次跨库向量 SQL
```

距离排序和 `LIMIT` 作用于所有知识库的 Chunk，数据库直接返回全局候选，不需要先给每个知识库
固定配额。

## 6. 为什么仍然需要候选倍数

用户要求 `top_k=5` 时，Repository 不只取 5 条，而是默认取：

```text
candidate_limit = top_k × search_candidate_multiplier
                = 5 × 4
                = 20
```

原因是候选回来后还要：

- 删除低于可靠性阈值的结果。
- 删除不同知识库中的重复正文。
- 后续可能增加权限策略或重排序。

如果数据库只返回 5 条，过滤后可能只剩 1 条，虽然第 6–10 条里仍有可靠证据。候选倍数在召回
数量、数据库开销和最终质量之间做折中，RAG-6 会根据评测调整。

## 7. 去重和全局排序怎样执行

Repository 已按向量距离返回候选，但 Service 仍显式执行稳定排序：

```text
score 降序
→ knowledge_base_id
→ document_id
→ chunk_index
→ chunk_id
```

第一项决定相关性；后面的字段只在分数相同时提供确定顺序，避免相同输入偶尔返回不同引用顺序。

去重键使用 `content_hash`。如果同一段制度文本被上传到两个知识库：

```text
知识库 A，相似度 0.93，content_hash = X
知识库 B，相似度 0.82，content_hash = X
```

排序后先遇到 0.93，记录哈希 X；再次遇到 X 时跳过，所以保留分数最高的来源。这样不会把相同
正文重复塞进 Prompt，节省 Token。

去重发生在候选层，PostgreSQL 中的原始 Chunk 不会被删除。

## 8. 归档知识库为什么是“跳过”而不是“报错”

用户绑定知识库时，PUT 接口禁止新绑定已归档知识库。但知识库可能在绑定之后才归档：

```text
会话绑定知识库 A
→ 后来归档 A
→ 用户继续在会话中提问
```

此时让整个聊天返回 409 会使普通对话也无法使用。因此会话适配层：

- 管理 GET 仍展示 A，方便用户发现并移除。
- 聊天检索跳过 A，不读取它的 Chunk。
- 结果记录 `skipped_archived_knowledge_base_ids`，方便日志、调试和未来前端提示。

如果所有绑定都已归档，结果是正常的空证据，不调用 Embedding。下一模块会根据空证据执行明确
拒答，而不是让模型凭空回答。

## 9. 所有权保护在哪里

跨用户隔离不是只做一次：

1. `ConversationRepository` 使用 `conversation_id + user_id` 查询会话。
2. 绑定 Repository 的 SQL 同时过滤会话和知识库的 `user_id`。
3. 核心 `search_many` 再检查每个知识库实体的 `user_id`。
4. 最终 pgvector SQL 仍过滤 `knowledge_bases.user_id`。

这种方式称为纵深防御。即使某个上游内部调用以后写错，越权实体也会在产生 Embedding 调用或
执行向量查询之前被阻止，并统一返回 404，避免泄露其他用户资源是否存在。

## 10. 旧向量配置检查

知识库实体记录当前冻结配置，但数据库里可能因为旧任务、错误脚本或配置变更残留其他模型的
Chunk。多库检索在调用 Embedding 前执行一次检查：

```text
document_chunks.embedding_model != knowledge_bases.embedding_model
```

只要任一活动知识库存在这种 READY Chunk，整次检索返回 409，不悄悄跳过问题知识库。静默返回
部分证据会让用户误以为系统已经搜索了全部资料，比明确失败更危险。

## 11. 正常、失败和空结果路径

| 场景 | 行为 | 是否调用 Embedding |
| --- | --- | --- |
| 两个同配置活动知识库 | 一次跨库搜索并全局 Top-K | 1 次 |
| 没有绑定 | 返回空证据 | 否 |
| 绑定全部已归档 | 记录跳过项并返回空证据 | 否 |
| 部分归档 | 只搜索活动知识库 | 1 次 |
| 会话不存在或跨用户 | 返回 404 | 否 |
| 内部误传跨用户知识库 | 返回 404 | 否 |
| 活动知识库配置不同 | 返回 409 | 否 |
| 任一知识库存在旧模型 Chunk | 返回 409 | 否 |
| Embedding Provider 故障 | 使用已有安全映射返回 503 | 已尝试 1 次 |
| 没有结果达到阈值 | 正常返回空证据 | 1 次 |

## 12. 为什么暂时不并行搜索

本模块所有活动知识库必须使用同一向量配置，因此可以通过一次 SQL 完成，不存在需要并行的多次
数据库搜索。

如果未来允许按不同模型分组，就可能并行生成多个查询向量。但同时要解决：

- 不同模型分数无法直接比较。
- 一个 Provider 失败时是否允许部分结果。
- 并发限流和取消如何传播。
- 多个任务不能并发使用同一个 SQLAlchemy `AsyncSession`。

这些问题需要评测和明确产品规则，第一版不提前引入。

## 13. 关键代码入口

- 跨库内部候选：
  `backend/app/modules/knowledge/search_models.py`
- 统一结果契约：
  `backend/app/modules/knowledge/schema/knowledge_search_schema.py`
- 一次跨库 pgvector SQL：
  `backend/app/modules/knowledge/repository/knowledge_search_repository.py`
- 配置校验、Embedding、去重与全局排序：
  `backend/app/modules/knowledge/service/knowledge_search_service.py`
- 会话结果契约：
  `backend/app/modules/conversation/schema/conversation_knowledge_search_schema.py`
- 会话所有权和绑定适配：
  `backend/app/modules/conversation/service/conversation_knowledge_search_service.py`
- 行为测试：
  `backend/tests/test_conversation_multi_knowledge_search.py`

## 14. 测试与真实数据库验收

自动测试覆盖：

- 跨库 SQL 的 ID、所有权、归档、READY、模型和距离排序条件。
- 一次检查多个知识库的旧模型 Chunk。
- 多知识库只生成一次查询向量。
- 可靠性阈值、正文哈希去重、稳定排序和全局 Top-K。
- 不同向量配置在调用 Provider 前失败。
- 内部误传跨用户实体时的二次保护。
- 空选择不调用 Embedding 和数据库向量查询。
- 会话 404 和归档绑定跳过。
- 原有单知识库检索全部回归。

全量结果：

```text
303 passed
Ruff: All checks passed!
```

真实 PostgreSQL 验收使用已有完成入库的知识库，并在同一个未提交事务中临时创建第二个同配置
知识库、READY 文档、Chunk 和会话绑定：

```text
实际搜索知识库：2
返回可靠证据：5
证据涉及知识库：2
最高相似度：1.0
```

验证后执行 `rollback`，再次查询临时知识库残留数为 0。正式用户、会话、知识库和文档数据均未
改变。

## 15. 本模块尚未做什么

- 没有把证据写入聊天 Prompt。
- 没有实现“无可靠证据时拒答”。
- 没有给证据分配 `[S1]`、`[S2]` 编号。
- 没有在助手消息事务中保存 `message_citations`。
- 没有扩展 SSE `sources` 事件。
- 没有前端引用卡片。

下一小步是 **RAG-5D：Grounded Prompt 与无证据拒答**。先让普通非流式聊天能够根据证据回答，
并证明无证据时不会调用聊天 LLM；之后再处理引用持久化与 SSE。

## 16. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 绑定 5 个库就调用 5 次 Embedding | 没有识别共享配置，应复用同一查询向量 |
| 不同模型结果直接按 score 排序 | 分数空间不可比较，应拒绝或引入统一重排序器 |
| 每个知识库各取 Top-5 后简单拼接 | 得到的不是全局 Top-K，且数据库往返更多 |
| Prompt 出现重复制度文本 | 缺少基于 `content_hash` 的候选去重 |
| 归档一个库后聊天整体 409 | 会话层应跳过后来归档的绑定并记录状态 |
| 无绑定仍调用 Embedding | 应在空集合分支直接返回空证据 |
| SQL 只按知识库 ID 过滤 | 必须同时过滤用户、未归档、READY 和模型 |
| 联调脚本在 Windows 连接 psycopg 失败 | 使用项目本地入口相同的 Selector 事件循环 |

## 17. 面试表达

> 我把会话绑定解析与核心向量检索分开：会话 Service 负责所有权和归档状态，知识检索 Service
> 负责向量配置、可靠性和排序。同配置的多个知识库只生成一次查询向量，并通过知识库 ID 的 IN
> 条件执行一次 pgvector 全局距离查询。候选按阈值过滤、稳定排序，再以内容哈希去重后取 Top-K。
> 不同 Embedding 模型的分数不可直接比较，所以第一版在调用 Provider 前明确返回 409，而不是
> 生成看似合理但不可靠的混排结果。最终 SQL 仍包含用户所有权、归档、READY 状态和模型过滤，
> 形成纵深隔离。

## 18. 小练习（约 15–20 分钟）

在 `backend/tests/test_conversation_multi_knowledge_search.py` 中增加：

```text
test_conversation_search_with_only_archived_bindings_returns_empty_evidence
```

练习目标：证明会话只绑定了一个已归档知识库时，不会报错，也不会把归档实体传给核心检索。

建议步骤：

1. 使用 `build_conversation` 创建当前用户会话。
2. 使用 `build_knowledge_base(..., is_archived=True)` 创建归档知识库。
3. 让绑定 Repository 返回这个知识库。
4. 让核心 Service 的 `search_many` 返回空的 `MultiKnowledgeSearchResponse`。
5. 调用会话 Service 的 `search`。
6. 断言传给 `search_many` 的 `knowledge_bases == []`。
7. 断言 `searched_knowledge_base_ids == []`。
8. 断言归档 ID 出现在 `skipped_archived_knowledge_base_ids`。
9. 断言 `results == []`。
10. 运行：
    `uv run pytest tests/test_conversation_multi_knowledge_search.py -q`

答案要点：归档是会话检索的可降级状态，不是整个聊天的致命错误；空活动集合必须在 Embedding 前
短路，后续 Grounded Chat 再根据空证据执行拒答。
