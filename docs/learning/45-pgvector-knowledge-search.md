# pgvector 知识检索、可靠性阈值与权限过滤

## 1. 本模块解决什么问题

上一模块已经能把文档处理成 Chunk，并保存 1024 维向量，但这些向量还不能被用户查询。
本模块补齐第一条真正的知识检索链路：

```text
POST /api/v1/knowledge-bases/{id}/search
→ JWT 恢复当前用户
→ KnowledgeSearchService 验证知识库所有权
→ 使用知识库 Embedding 快照生成问题向量
→ KnowledgeSearchRepository 执行 pgvector 余弦检索
→ Service 按可靠性阈值过滤并截取 Top-K
→ 返回文件、页码、Chunk、原文和分数
```

本模块只返回证据，不调用聊天大模型。下一模块才会把证据放入 Prompt，完成带引用回答和
无证据拒答。

## 2. 为什么先检查所有权，再调用 Embedding

如果先生成查询向量，再查询知识库是否属于当前用户，会产生两个问题：

- 越权请求也会消耗真实模型额度。
- 响应耗时可能泄露目标资源是否存在。

因此 Service 先执行：

```text
WHERE knowledge_bases.id = :knowledge_base_id
  AND knowledge_bases.user_id = :current_user_id
```

不存在和跨用户访问都返回同一个 `404 KNOWLEDGE_BASE_NOT_FOUND`。Repository 的向量 SQL
仍然再次携带 `user_id` 条件，形成纵深保护，防止未来调用方绕过 Service 后直接扩大查询
范围。

## 3. 查询向量为什么必须使用知识库快照

知识库创建时已经冻结：

- `embedding_provider`
- `embedding_model`
- `embedding_dimension`

文档向量和问题向量只有来自同一套配置才可以比较。例如，不能把旧的
`mock-hash-1024` 文档向量和新的 `text-embedding-v4` 问题向量放在一起排序。

检索前会检查 READY 文档是否混入不同 `embedding_model`。发现不一致时返回
`409 KNOWLEDGE_SEARCH_CONFIG_MISMATCH`，提示重新入库，而不是静默产生错误答案。

数据库列固定为 `vector(1024)`，EmbeddingService 还会校验模型名、返回数量、维度和非有限
数值，避免错误向量进入查询。

## 4. pgvector 余弦距离怎样变成相似度

Repository 使用 pgvector 的余弦距离运算符：

```text
embedding <=> query_vector
```

距离越小越相似。为了让 API 中的数字更符合直觉，代码转换成：

```text
similarity = 1 - cosine_distance
```

因此结果按距离升序排列，响应中的 `score` 越大表示词面或语义越接近。浮点计算偶尔可能
产生极小的边界误差，Repository 会把分数收敛到 `[-1, 1]`。

## 5. 为什么 SQL 还要过滤 READY 和模型

向量查询同时限制：

```text
当前用户
AND 指定知识库
AND 知识库未归档
AND document.status = READY
AND chunk.embedding_model = 知识库模型快照
```

`PENDING`、`PROCESSING` 和 `FAILED` 文档都不能参与检索。否则用户可能拿到半套索引、旧
Chunk 或已经确认解析失败的内容。

## 6. 为什么先多召回，再按阈值过滤

用户请求 `top_k = 5` 时，Repository 默认先取 `5 × 4 = 20` 个候选，Service 再执行：

```text
score >= search_similarity_threshold
→ 保留前 top_k 条
```

这叫候选过采样。以后加入文档级过滤、去重或重排序时，即使部分候选被淘汰，仍有机会
凑齐可靠结果。

客户端不能自行传入阈值。否则调用者可以把阈值降得很低，让无关内容伪装成证据。当前
`0.2` 只是 Mock 演示初值，不代表所有模型都适用；RAG-6 会使用评测集观察 Recall@K、MRR、
拒答准确率和引用有效率后重新校准。

## 7. Router、Service 和 Repository 各负责什么

### Router

- 接收 `query` 和 `top_k`。
- 使用 JWT 注入的当前用户。
- 声明 HTTP 请求与响应模型。

### Service

- 校验所有权和归档状态。
- 选择知识库 Embedding 快照。
- 调用 EmbeddingService。
- 处理模型错误和配置不一致。
- 执行阈值与 Top-K 业务规则。

### Repository

- 构造 pgvector SQL。
- 在 SQL 层限制用户、知识库、文档状态和模型。
- 返回内部 `KnowledgeSearchCandidate`，不决定 HTTP 错误和可靠性阈值。

检索是只读操作，因此本调用链不执行 `commit()`。Session 在 HTTP 请求结束后关闭；发生异常
时通用数据库依赖会回滚可能残留的事务状态。

## 8. 正常、空结果和失败怎样执行

### 正常

问题向量生成成功，数据库返回候选，Service 过滤后返回有序来源。

### 没有可靠证据

接口仍返回 `200`，但 `results = []`。这是正常业务结果，不是服务器错误。下一模块会据此
跳过大模型调用并生成明确拒答。

### Provider 暂时失败

第三方异常转换为 `503 KNOWLEDGE_SEARCH_PROVIDER_UNAVAILABLE`。响应不会泄露 API Key、
第三方响应正文或内部堆栈。

### 配置不一致

返回 `409 KNOWLEDGE_SEARCH_CONFIG_MISMATCH`，要求重新入库文档，避免混用向量。

## 9. 关键代码入口

- API 契约：
  `backend/app/modules/knowledge/schema/knowledge_search_schema.py`
- HTTP 路由：
  `backend/app/modules/knowledge/api/knowledge_base_router.py`
- 业务编排：
  `backend/app/modules/knowledge/service/knowledge_search_service.py`
- pgvector SQL：
  `backend/app/modules/knowledge/repository/knowledge_search_repository.py`
- 内部候选模型：
  `backend/app/modules/knowledge/search_models.py`
- 依赖注入：
  `backend/app/modules/knowledge/dependencies.py`
- 行为测试：
  `backend/tests/test_knowledge_search.py`

## 10. 本模块测试保护什么

- 请求去空格、空问题、Top-K 上下界。
- SQL 必须包含用户所有权、READY 状态、模型和余弦排序。
- 越权时不调用 Embedding。
- 归档库不能搜索。
- 模型混用时拒绝检索。
- Provider 错误不会泄露内部细节。
- 阈值过滤、Top-K 和来源响应结构正确。
- HTTP 路由受 Bearer Token 保护。

真实联调还验证了：正确中文 Markdown 入库后，查询“北极星项目会议时间”能够命中包含
“周五下午三点”的 Chunk。

## 11. 常见错误

| 现象 | 排查方向 |
| --- | --- |
| 一直返回空结果 | 文档是否 READY、查询与 Chunk 是否有词面重叠、阈值是否过高 |
| Mock 结果不够智能 | Mock 只使用稳定词项哈希，不具备真正语义理解 |
| 返回配置不一致 | 检查知识库快照与 Chunk 的 `embedding_model`，重新入库旧文档 |
| 跨用户请求返回 404 | 这是避免泄露资源存在性的预期设计 |
| 中文变成问号 | 检查测试终端输入编码；HTTP 前端 UTF-8 上传不受该问题影响 |

## 12. 面试表达

> 我把检索核心实现为独立 Service，而不是直接写进聊天或 MCP Tool。请求先按当前用户校验
> 知识库所有权，再按知识库冻结的 Embedding 配置生成问题向量。pgvector SQL 同时过滤
> 用户、READY 文档和模型，并使用 HNSW 余弦索引召回候选。Service 负责阈值和 Top-K，
> 空结果是正常业务状态。这样普通聊天、Knowledge Agent 和后续 MCP Server 都能复用同一
> 检索能力，并且越权请求不会消耗模型额度。

## 13. 小练习（约 15 分钟）

请在 `backend/tests/test_knowledge_search.py` 增加一个边界测试：

1. 设置 `similarity_threshold = 0.5`。
2. 构造两个候选，分数分别为 `0.5` 和 `0.4999`。
3. 断言分数正好等于阈值的候选被保留，低于阈值的候选被过滤。
4. 不修改生产代码，先判断现有实现是否已经满足要求。

答案要点：业务规则使用 `score >= threshold`，所以 `0.5` 应保留；`0.4999` 应过滤。这个练习
用于理解边界值测试，而不是重复测试普通成功路径。
