# 67. RAG 两阶段检索与 EvidenceSelector 契约

## 这一模块解决什么问题

原来的 `KnowledgeSearchService` 完成向量查询后，直接执行：

```text
候选分数 >= 阈值
→ 截取 Top-K
→ 返回给回答层
```

它把两个不同问题混在一起：

1. 哪些 Chunk 与问题相关？
2. 哪些 Chunk 真正包含足以回答问题的证据？

困难评测已经证明，真实 Embedding 很擅长第一个问题，却无法独立解决第二个问题。因此本模块把检索拆成两个阶段，但暂时保持线上结果不变。

## 当前数据流

```text
HTTP / 会话检索
→ KnowledgeSearchService
→ 权限与向量配置校验
→ EmbeddingService 生成查询向量
→ KnowledgeSearchRepository 使用 pgvector 召回候选
→ EvidenceSelector 重排、筛选并决定是否可回答
→ KnowledgeSearchResponse
→ 普通搜索或聊天 RAG
```

单知识库和多知识库都使用同一个 Selector。多知识库路径会先按正文哈希去掉重复 Chunk，再进入第二阶段。

## 为什么只定义一个 Selector

可以一开始就拆成：

```text
CandidateRetriever
Reranker
AnswerabilityJudge
EvidencePolicy
```

这些名称都合理，但当前还没有第二种正式算法。如果现在创建四层接口，很多抽象只能靠猜测设计。

本模块只定义 `BaseEvidenceSelector`，把“重排、删减和答案充分性决定”放在一个稳定边界里。等真实 Reranker 和 Judge 的输入输出差异被实验验证后，再决定是否继续拆分。

## 三个核心契约

### EvidenceSelectionRequest

它包含：

- `query`：当前用户问题。
- `candidates`：第一阶段召回的完整候选元组。
- `top_k`：最多返回多少条证据。
- `similarity_threshold`：当前 Embedding Provider 的粗过滤阈值。

即使 HTTP 已经校验过 Top-K，内部对象仍会再次校验。因为 Selector 也可能被 Worker、Agent、MCP Tool 或测试直接调用。

### EvidenceSelectionResult

它包含：

- `answerable`：是否允许进入有证据回答流程。
- `selected_candidates`：最终有序证据。
- `reason`：选择或拒绝的原因。

当前原因包括：

- `selected`：存在可用证据。
- `no_candidates`：第一阶段完全没有候选。
- `below_threshold`：有候选，但都低于基础阈值。
- `selector_rejected`：第二阶段主动判断证据不足。

结果对象要求 `answerable` 与候选集合一致：有最终证据时必须为 `true`，没有证据时必须为 `false`。

### BaseEvidenceSelector

它只定义一个异步方法：

```python
async def select(request) -> EvidenceSelectionResult:
    ...
```

当前阈值实现并不访问网络，理论上可以写成同步函数。但真实 Reranker 或 LLM Judge 会进行异步 HTTP 请求。现在统一为异步接口，Service 将来替换实现时不需要改变调用方式。

## Protocol 和泛型如何理解

单知识库候选是 `KnowledgeSearchCandidate`，多知识库候选是 `MultiKnowledgeSearchCandidate`。它们字段不完全一样，但 Selector 最少只需要：

```text
chunk_id
score
```

`EvidenceCandidate Protocol` 的意思不是创建一个新的父类，而是声明：

> 只要某个对象拥有符合类型要求的 `chunk_id` 和 `score`，它就能作为候选使用。

这叫结构化类型。现有两个 dataclass 不需要修改继承关系。

泛型 `CandidateT` 用于保证输入什么候选类型，输出仍是什么候选类型：

```text
输入 KnowledgeSearchCandidate
→ 输出 KnowledgeSearchCandidate

输入 MultiKnowledgeSearchCandidate
→ 输出 MultiKnowledgeSearchCandidate
```

Selector 不会把多知识库来源信息弄丢。

## 默认阈值实现

`ThresholdEvidenceSelector` 负责复现重构前的行为：

1. 按向量分数稳定降序排序。
2. 移除低于 Provider 阈值的候选。
3. 截取 Top-K。
4. 根据结果生成明确原因。

它不是最终的答案充分性方案。保留它的原因是让架构重构与算法实验分开：如果这一轮测试失败，可以确定是代码分层问题，而不是新算法效果问题。

## Mock 实现

`MockEvidenceSelector` 支持：

- 按指定 Chunk ID 改变最终顺序。
- 强制判定证据不足。
- 模拟第二阶段服务失败。
- 记录收到的请求供测试断言。

Mock 不会自动成为生产 Selector。它的价值是让测试能稳定覆盖所有分支，不需要外部 Reranker、API Key 或网络。

## 为什么 Selector 不能返回任意 Chunk

第二阶段只允许重新排列或删除第一阶段候选，不能凭空创建一个 Chunk。`ensure_valid_for` 会检查：

- 返回数量没有超过 Top-K。
- 每个最终 Chunk 都来自本次第一阶段候选。
- 最终结果没有重复 Chunk。

这既是程序正确性规则，也是权限边界。第一阶段 SQL 已执行用户所有权过滤，Selector 如果能注入候选集合外的 Chunk，就可能绕开这层隔离。

## 正常拒答和系统失败

这是本模块最重要的错误边界：

```text
没有候选 / 证据不足
→ 正常业务结果
→ results = []
→ 聊天层生成明确拒答

Selector 超时 / 协议错误 / 返回伪造 Chunk
→ 系统故障
→ 503 KNOWLEDGE_SEARCH_SELECTOR_UNAVAILABLE
→ 不能伪装成正常拒答
```

如果把故障吞掉并返回空结果，用户会误以为“知识库里没有答案”，运维也无法知道第二阶段服务已经不可用。

错误日志只记录 Selector 类名，不记录异常正文，避免第三方地址、请求正文或密钥进入日志。

## 数据库事务在哪里

本模块没有开启写事务，因为检索链路只读取：

- 知识库所有权和状态。
- Chunk 与向量。
- Embedding 配置快照。

Repository 执行只读 SQL，Selector 处理内存中的候选。最终聊天消息和引用的写事务仍在消息持久化模块中，不应该由检索 Service 提前提交。

## 单知识库与多知识库的区别

单知识库：

```text
pgvector 候选
→ Selector
```

多知识库：

```text
一次全局 pgvector 查询
→ 按正文哈希去重
→ Selector
```

去重放在 Selector 前，是因为同一份资料可能被上传到两个知识库。重复正文不应该占用第二阶段候选预算和最终 Top-K。

## 测试分别保护什么

- 阈值 Selector：排序、阈值、Top-K 和拒绝原因。
- Mock Selector：可控重排、主动拒答和故障。
- 边界校验：禁止返回第一阶段之外的 Chunk。
- Service 集成：第二阶段顺序真正进入 HTTP 响应。
- 故障映射：Selector 失败返回安全 503，不泄露内部正文。
- 多知识库回归：全局排序和正文去重行为不变。
- 全量回归：聊天、SSE、引用和评测继续通过。

## 本轮遇到的典型错误

多知识库测试单独运行时，SQLAlchemy 无法解析 `ConversationEntity` 字符串关联的 `MessageEntity`。全量测试以前能通过，只是因为其他测试碰巧先导入了全部模型。

修复方式是从 `app.modules.conversation.models` 聚合入口导入实体，让测试不依赖执行顺序。

这类问题的判断方法是：如果同一个测试单独失败、全量运行却通过，优先检查全局缓存、导入顺序、环境变量和共享数据库状态。

## 替代方案与取舍

### 继续在 Service 写 if

代码更少，但每增加一个 Reranker 或 Judge，Service 会不断出现 Provider 分支，难以独立测试。

### 立即使用 LangChain Retriever

能快速组合组件，但会把当前清晰的 Repository 权限查询和项目内部响应契约包进新的框架抽象。当前阶段没有必要。

### 直接使用 LLM 判断

最容易理解“是否回答了问题”，但增加费用、延迟和非确定性。应先建立接口和 Mock，再通过困难集决定是否值得接入。

## 面试时如何表达

> 困难集证明相似度不能等价于证据充分性，所以我没有继续把逻辑堆在 SearchService 中。我保留 pgvector 作为第一阶段候选召回，再抽象异步 EvidenceSelector 负责重排和答案充分性决定。默认阈值实现保证重构不改变线上行为，Mock 实现覆盖改序、拒答和故障。Selector 只能删减第一阶段已通过权限过滤的候选，失败会返回 503，不能伪装成知识库无答案。

## 小练习

预计 15–20 分钟：

请解释下面两个结果为什么不能使用同一种处理方式：

```text
A. reason = selector_rejected，selected_candidates = ()
B. Selector 抛出 EvidenceSelectionError
```

答案要点：A 是系统正常运行后得出的业务结论，应形成可解释拒答；B 表示系统没有完成判断，应返回可重试错误并记录监控，不能告诉用户“资料中没有答案”。
