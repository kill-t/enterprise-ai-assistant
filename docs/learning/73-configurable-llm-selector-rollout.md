# 73. LLM 证据选择器的可配置灰度接入

## 这一模块解决什么问题

前一个模块已经通过真实评测证明：

```text
向量宽召回 + DeepSeek V4 Flash EvidenceSelector
```

明显优于固定向量阈值。但当时代码仍只有评测命令会创建 `LLMEvidenceSelector`，真正的知识搜索 API 和会话聊天仍固定使用阈值实现。

本模块把经过验证的能力接入在线调用链，同时保留明确开关：

```text
threshold：稳定、本地、无 LLM 费用
llm：宽召回 + DeepSeek 证据充分性判断
```

默认仍为 `threshold`，所以拉取项目、运行测试或普通本地演示不会自动产生费用。

## 在线调用链

```text
RAG 配置
  ↓
create_configured_evidence_selector
  ├─ threshold → ThresholdEvidenceSelector
  └─ llm       → DeepSeekProvider + LLMEvidenceSelector
  ↓
create_knowledge_search_service
  ├─ 知识库搜索 API Dependency
  └─ ConversationKnowledgeSearchService
  ↓
KnowledgeSearchService
  ↓
向量候选 → EvidenceSelector → 来源或拒答
```

普通搜索接口与聊天 RAG 最终复用同一个 `KnowledgeSearchService` 工厂，所以不会出现一个入口已经使用 LLM、另一个入口仍使用阈值的配置漂移。

## 为什么需要两层工厂

### EvidenceSelector 工厂

它只负责回答：第二阶段使用哪种算法？

```text
RAG__EVIDENCE_SELECTOR=threshold
→ 直接创建 ThresholdEvidenceSelector
→ 不创建 DeepSeekProvider

RAG__EVIDENCE_SELECTOR=llm
→ 创建 DeepSeekProvider
→ 使用配置中的模型、超时、思考模式和重试次数
→ 创建 LLMEvidenceSelector
```

### KnowledgeSearchService 工厂

它负责把完整搜索链组装起来：

- Embedding Provider Factory。
- 候选倍率。
- 相似度阈值策略。
- 当前 EvidenceSelector。

如果把这些代码分别写在搜索 Router、会话 Service 和未来 Agent Tool 中，同一配置很容易组装出三种不同行为。共享工厂把差异集中在一个入口。

## 配置项

本地默认配置：

```text
RAG__EVIDENCE_SELECTOR=threshold
RAG__LLM_EVIDENCE_SELECTOR_MODEL=deepseek-v4-flash
RAG__LLM_EVIDENCE_SELECTOR_TIMEOUT_SECONDS=30
RAG__LLM_EVIDENCE_SELECTOR_THINKING_ENABLED=false
RAG__LLM_EVIDENCE_SELECTOR_MAX_OUTPUT_RETRIES=1
```

只有第一项决定当前是否真实使用 LLM，其余配置在 `threshold` 模式不会创建任何远程客户端。

启用真实 Selector：

```text
DEEPSEEK__API_KEY=真实密钥
RAG__EVIDENCE_SELECTOR=llm
```

修改 `.env` 后必须重启后端，因为 Settings 在进程启动时加载和缓存。

## 为什么 LLM 模式使用 similarity_threshold=-1

固定阈值方案的数据流：

```text
向量候选
→ score >= 当前 Embedding 阈值
→ Top-K
```

LLM 方案的数据流：

```text
向量宽召回
→ LLM 判断答案充分性
→ 最多 Top-K
```

如果 LLM 前仍然执行旧 `0.45` 阈值，评测中被证明可以由 LLM 找回的低分正确证据会先被删除。因此共享 Service 工厂在 LLM 模式显式传入 `-1.0`，表示候选进入第二阶段前不应用旧阈值。

这个值也会出现在搜索响应的调试元数据中，避免前端或日志错误声称本次用了 `0.45`。

## 为什么缺少密钥要在启动时失败

错误方式：

```text
后端看似正常启动
→ 用户上传资料并提问
→ 第一次调用才发现没有 DeepSeek Key
→ 返回 503
```

当前方式：

```text
FastAPI lifespan
→ 校验 JWT
→ 校验 RAG Selector 运行配置
→ LLM 模式没有有效 DeepSeek Key
→ 立即用中文错误终止启动
```

配置错误不是偶发网络故障，越早发现越容易调试。`threshold` 模式不要求 DeepSeek Key，因为这一模式根本不会创建远程 Provider。

## “可回退”不等于“自动降级”

项目保留 `threshold`，意味着运维或开发者可以修改配置并重启，主动回退到稳定方案。

它不表示每个请求遇到 DeepSeek 超时后自动使用阈值：

```text
LLM 配置启用 + DeepSeek 超时
→ EvidenceSelectionError
→ KnowledgeSearchService 转换为 503
→ 用户知道系统没有完成可靠判断
```

如果自动退回阈值并返回答案，调用方无法知道这次回答使用了较弱策略，也无法解释同一个问题为什么有时拒答、有时放行。以后若要增加自动降级，必须让响应、审计和前端明确展示实际策略，不能静默发生。

## 搜索 API 与聊天为什么必须同步切换

项目有两个入口使用核心检索：

```text
POST /knowledge-bases/{id}/search
→ KnowledgeSearchService

会话发送消息
→ ConversationService
→ ConversationKnowledgeSearchService
→ KnowledgeSearchService.search_many
```

之前第二条路径会在 Service 构造函数内部创建自己的 `KnowledgeSearchService`，它不知道搜索 API Dependency 的选择。现在两者都调用 `create_knowledge_search_service`，因此同一个环境变量决定两条链路。

未来 Knowledge Agent 和 MCP `search_knowledge` 也应复用这个工厂或直接复用核心 Service，不能各自实现第二阶段策略。

## 数据库事务有没有变化

没有新增表，也没有数据库迁移。

本模块只改变依赖组装：

- pgvector 查询仍使用当前请求的 `AsyncSession`。
- Selector 只读取候选并调用模型，不写数据库。
- 聊天成功后保存消息和引用的原有事务边界不变。
- Selector 失败发生在助手成功消息提交前，继续走现有失败消息状态处理。

## 关键代码入口

- `backend/app/core/config.py`：RAG Selector 配置与启动校验。
- `backend/app/main.py`：FastAPI lifespan 启动检查。
- `backend/app/modules/knowledge/retrieval/evidence_selector_factory.py`：阈值与 LLM 实现选择。
- `backend/app/modules/knowledge/service/knowledge_search_factory.py`：共享核心搜索 Service 组装。
- `backend/app/modules/knowledge/dependencies.py`：知识搜索 API 入口。
- `backend/app/modules/conversation/service/conversation_knowledge_search_service.py`：聊天 RAG 入口。
- `backend/tests/test_evidence_selector_configuration.py`：配置与两条入口一致性测试。

## 测试保护什么

- 默认配置为 `threshold`。
- 阈值模式绝不创建 DeepSeek Provider。
- LLM 模式缺少真实密钥时启动失败。
- 空模型名、非法超时和重试次数由 Pydantic 拒绝。
- 模型名、输入预算、超时、思考模式和重试次数真实进入 Selector。
- LLM 模式关闭旧向量阈值，阈值模式继续动态读取 Embedding 阈值。
- 搜索 API Dependency 使用共享工厂。
- 会话 RAG 使用同一个共享工厂。
- 第二阶段异常继续由现有 Service 映射为安全 503。

## 面试时可以怎么讲

> 真实评测证明 LLM EvidenceSelector 有净提升后，我没有直接删除阈值方案，而是增加配置化灰度。默认 threshold 不创建远程客户端；启用 llm 时，搜索 API 和会话 RAG 通过同一个 Service 工厂切换到 DeepSeek V4 Flash，并关闭旧向量阈值进行宽召回。缺少密钥会在 FastAPI 启动阶段失败，运行时模型故障返回 503，不会静默降级成较弱答案。这样既能控制费用，也能避免不同入口产生策略漂移。

## 15–30 分钟小练习

请用自己的话回答：

1. 为什么只修改 `knowledge.dependencies` 不能保证聊天也使用 LLM Selector？
2. 为什么 LLM 模式需要把旧相似度阈值设为 `-1.0`？
3. 为什么“保留 threshold 回退能力”和“请求失败时自动 threshold 降级”不是一回事？

答案要点：聊天原本会自己构造核心 Service；旧阈值会在 LLM 判断前误删候选；前者是显式配置切换，后者会让单次请求悄悄改变可靠性策略。
