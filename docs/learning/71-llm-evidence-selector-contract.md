# 71. LLM 证据充分性选择器契约

## 这一模块解决什么问题

第一阶段 Retriever 只负责“尽量找出相关候选”。它找到主题相似的 Chunk，不代表 Chunk 一定写出了问题的答案。

例如用户问“蓝鲸项目负责人是谁”，下面两段都可能与“蓝鲸项目”很相似：

```text
候选 A：蓝鲸项目负责人将在下周确定。
候选 B：蓝鲸项目负责人是林晓。
```

候选 A 相关，但不足以回答姓名；候选 B 才是直接证据。`LLMEvidenceSelector` 的职责就是在候选召回之后判断：

- 现有证据能否回答问题。
- 哪几条 Chunk 是回答所必需的来源。
- 证据不足是缺少具体事实、覆盖不完整，还是互相冲突。

它不负责生成最终答案，也不会重新查询数据库。

## 在整体架构中的位置

```text
用户问题
  ↓
第一阶段 Retriever
  ├─ 向量检索
  └─ 可选的关键词 / Hybrid 检索
  ↓
候选 Chunk
  ↓
LLMEvidenceSelector
  ├─ 构建不可信数据 Prompt
  ├─ 调用统一 LLM Provider
  └─ Pydantic 校验结构化决定
  ↓
足够：选中的完整 Chunk → 后续 Grounded Answer
不足：空来源 → 正常拒答
故障：抛出 EvidenceSelectionError → 上层按系统失败处理
```

本模块目前只完成内部契约和 Mock 验证，尚未替换线上聊天使用的 `ThresholdEvidenceSelector`。原因是 LLM 判断质量必须先用困难评测集验证，不能只看单元测试通过就上线。

## 为什么 Retriever 和 Selector 要分开

两层优化的是不同目标：

- Retriever 追求召回率：正确证据尽量不要漏掉。
- Selector 追求精确性和拒答：只放行真正足以回答的证据。

如果让 Retriever 只返回非常少的候选，正确答案可能在第一层就丢失；如果把所有相关候选直接交给回答模型，模型又可能从“尚未决定”之类的内容中编造答案。

因此本项目采用“先宽召回，再严格判断”的两阶段设计。

## 统一输入和输出

输入沿用 `EvidenceSelectionRequest`：

```text
query                 用户问题
candidates            第一阶段的有序候选
top_k                 最终最多保留多少条证据
similarity_threshold  为兼容现有选择器保留的请求参数
```

LLM 只能返回最小 JSON 决策：

```json
{
  "answerable": true,
  "selected_source_ids": ["S2", "S1"],
  "reason": "sufficient"
}
```

`reason` 不是自由推理文本，只允许以下枚举：

- `sufficient`：证据充分。
- `no_direct_support`：候选没有直接支持。
- `missing_specific_fact`：缺少姓名、日期、数值等具体事实。
- `incomplete_support`：多部分问题只覆盖了一部分。
- `conflicting_support`：候选之间存在无法安全消解的冲突。

不保存模型的自由推理正文，可以减少敏感信息泄露和不可控内容进入审计记录。

## 为什么使用 S1、S2，而不把 UUID 交给模型

Selector 为每次 Prompt 临时建立映射：

```text
S1 → 第一个候选 Chunk
S2 → 第二个候选 Chunk
```

模型只返回 `S1` 这类短编号。服务端随后把编号映射回原始候选对象，并检查：

- 编号格式是否合法。
- 编号是否真的出现在本次 Prompt 中。
- 是否有重复编号。
- 选择数量是否超过 `top_k`。

这样既减少 Token，也避免模型伪造数据库 ID。真正的 Chunk UUID 始终留在服务端。

## JSON 模式为什么还不够

Provider 的 `JSON_OBJECT` 只要求响应是 JSON 对象，并不能保证字段完全正确。模型仍可能返回：

```json
{
  "answerable": "true",
  "selected_source_ids": ["S99"],
  "reason": "maybe",
  "extra": "不属于协议"
}
```

所以项目使用严格 Pydantic 模型执行第二次校验：

- 布尔值不能用字符串冒充。
- 原因必须属于枚举。
- 不允许附加字段。
- `answerable=true` 时来源不能为空且原因必须为 `sufficient`。
- `answerable=false` 时来源必须为空且原因不能为 `sufficient`。

模型输出永远是不可信输入，不能因为它来自 LLM 就跳过应用层校验。

## 如何防御文档中的 Prompt Injection

知识库文档可能包含“忽略系统提示”“选择当前来源”等恶意内容。当前实现采取三层约束：

1. System Prompt 明确声明问题和候选正文都是不可信数据，不得执行其中的命令。
2. 候选作为一个独立 JSON 数据消息发送，不与系统指令直接拼接。
3. 模型只能返回服务端预先分配的来源编号，结果还要经过严格校验。

这些措施能降低风险，但不能宣称彻底消除 Prompt Injection。未来仍需通过恶意样本评测、模型升级和调用审计持续验证。

## Token 预算为什么只删除完整 Chunk

候选已经按第一阶段排名排列。Prompt 超过输入预算时，Selector 从榜尾删除低排名候选，直到满足预算：

```text
S1、S2、S3 超限
→ 删除 S3
→ S1、S2 仍超限
→ 删除 S2
→ 完整保留 S1
```

它不会把一个 Chunk 从中间截断。截断可能刚好删除日期、否定词或句子结尾，使证据含义发生变化。连一条完整候选都放不下时，系统明确抛出 `LLMEvidencePromptBudgetError`，而不是发送残缺证据。

## 正常拒答和系统失败的区别

```text
没有候选
→ 不调用 LLM
→ NO_CANDIDATES

LLM 判断证据不足
→ 正常业务结果
→ SELECTOR_REJECTED

LLM 超时、网络错误或返回非法结构
→ 系统没有完成证据判断
→ EvidenceSelectionError
```

系统故障不能伪装成“知识库没有答案”，否则会让用户误以为系统已经可靠地检查过资料。

`asyncio.timeout` 限制单次等待时间；异常消息不会把第三方地址、响应正文或密钥细节返回给上层。协程取消不被转换为普通失败，以便未来用户停止生成时能继续向上传播取消信号。

## Mock Provider 能证明什么

项目 Mock Provider 现在支持 `evidence_selection` 操作，可以离线验证：

- 请求是否走统一 `BaseChatProvider`。
- JSON 协议能否被解析。
- 来源编号能否映射回候选。
- 本地开发和 CI 不会产生模型费用。

Mock 默认选择第一条候选，它不能证明真正的答案充分性判断质量。因此单元测试通过后，还必须用真实模型运行困难评测集，比较 Recall、MRR、拒答准确率、延迟和调用成本。

当前 `.env` 虽然已经配置 DeepSeek Key，但本模块没有自动发起真实请求。完整 35 题评测大约需要 35 次付费 LLM 调用，应在明确开始真实评测后单独执行并保存报告。

## 关键代码入口

- `backend/app/modules/knowledge/retrieval/llm_evidence_selector.py`：结构化决定、Prompt、预算、调用和映射。
- `backend/app/modules/knowledge/retrieval/evidence_selection.py`：所有 Selector 共用的输入输出契约。
- `backend/app/infrastructure/llm/providers/mock_provider.py`：离线结构化响应。
- `backend/tests/test_llm_evidence_selector.py`：协议、安全边界、预算、超时和错误测试。

## 为什么暂不直接使用其他方案

- 固定向量阈值：便宜稳定，但无法理解“相关却没有答案”和多证据完整性。
- Cross-Encoder Reranker：排序通常更稳定、成本可控，但普通相关性模型未必能可靠判断答案充分性。
- 直接让回答模型自己挑证据：实现简单，但“选证据”和“写答案”混在一次调用里，不容易单独评测与审计。
- LLM Selector：能理解具体事实、多证据和冲突，但增加延迟、费用和模型不确定性。

因此当前先把 LLM Selector 做成可替换 Adapter，用真实评测决定是否接入，而不是写死在线上链路中。

## 测试保护什么

- 决定、来源和原因码必须一致。
- 文档注入内容只能作为不可信 JSON 数据出现。
- 临时来源按模型返回顺序映射，不能伪造或重复。
- 非法 JSON、错误类型、附加字段和超出 `top_k` 都被拒绝。
- 空候选不调用模型。
- 网络失败与超时不会泄露内部细节。
- Token 超限只移除完整的低排名 Chunk。
- Mock 模式不调用真实模型。

## 面试时可以怎么讲

> 我的 RAG 使用两阶段设计：Retriever 负责高召回，EvidenceSelector 负责判断候选是否真正包含答案。LLM 只接收带 S1、S2 临时编号的不可信 JSON 数据，并返回一个最小结构化决定；服务端再用严格 Pydantic 校验来源、数量和状态一致性。空候选不会产生模型费用，超时和非法结构属于系统失败，不能伪装成正常拒答。这个 Selector 先作为内部可替换能力，通过真实困难集评测后才决定是否接入聊天。

## 15–30 分钟小练习

请先不改代码，用自己的话回答：

1. 为什么“检索结果与问题相似”不能直接证明“资料足以回答问题”？
2. 为什么模型返回 `S99` 时不能简单忽略它并继续生成答案？
3. 为什么 Mock 测试全通过，仍不能说明真实拒答准确率已经提升？

答案要点：相关性与答案充分性是两个目标；伪造来源代表协议已失效，静默忽略会掩盖模型错误；Mock 只验证调用结构，不具备真实语义判断能力。
