# 真实 Embedding 基线与相似度阈值校准

## 1. 本模块解决什么问题

Mock Embedding 能验证工程链路，但它主要依赖词和字符哈希，不能代表真实语义检索。

本模块完成：

```text
安全读取真实 API Key
→ 验证 text-embedding-v4 连通性
→ 新建独立真实向量知识库
→ 重新入库同一受控语料
→ 运行同一套 14 题
→ 分析相关与无关分数分布
→ 校准 Provider 独立阈值
→ 保存脱敏真实基线
```

## 2. 为什么必须新建知识库并重新入库

原 Mock 知识库保存的是：

```text
provider=mock
model=mock-hash-1024
dimension=1024
```

真实知识库保存的是：

```text
provider=openai_compatible
model=text-embedding-v4
dimension=1024
```

虽然维度都是 1024，但两个模型产生的向量不在同一个向量空间，不能混合比较。

正确流程：

```text
创建新知识库
→ 冻结真实 Embedding 快照
→ 原文重新解析和分块
→ 使用真实模型生成每个 Chunk 向量
→ 原子写入 pgvector
```

不能只修改 `.env` 后继续搜索旧 Mock Chunk。

## 3. API Key 如何安全使用

真实 Key 只保存在被 Git 忽略的 `backend/.env`：

```env
EMBEDDING__PROVIDER=openai_compatible
EMBEDDING__API_KEY=本机真实密钥
EMBEDDING__BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING__MODEL=text-embedding-v4
EMBEDDING__DIMENSION=1024
```

安全校验只输出布尔结果：

```text
Provider 是否正确
模型是否正确
维度是否正确
Key 是否存在
```

不输出 Key 正文、前后字符或长度。仓库的 `.env.example` 仍然不包含真实密钥。

## 4. 本次发现的配置类型错误

环境变量天然是字符串：

```text
EMBEDDING__DIMENSION=1024
→ Pydantic 收到 "1024"
```

旧代码使用：

```python
dimension: Literal[1024] = 1024
```

它在当前嵌套配置解析中没有先把字符串转换成整数，导致应用启动失败。

修复后：

```python
dimension: int = Field(default=1024, gt=0)

@field_validator("dimension")
def validate_database_dimension(value: int) -> int:
    if value != 1024:
        raise ValueError(...)
    return value
```

职责分成两步：

1. Pydantic 把字符串 `"1024"` 转换为整数。
2. 业务校验器阻止 768 等与数据库 `vector(1024)` 不兼容的维度。

对应测试同时保护合法字符串和非法维度。

## 5. 真实入库结果

受控 Markdown 经过真实 Worker 后：

```text
文档状态：READY
Chunk 数：5
Embedding 模型：text-embedding-v4
向量维度：1024
```

Worker 在外部 Embedding 全部成功后，才用一个数据库事务替换 Chunk 并更新文档终态。
如果中途失败，文档会进入 `FAILED`，不会出现只有部分 Chunk 的“半套索引”。

## 6. 为什么原来的 0.2 阈值失败

第一次真实评测仍使用 Mock 阶段的 `0.2`：

```text
Recall@3：100%
MRR：85%
拒答准确率：71.43%
```

10 条回答题全部成功，但 4 条拒答题全部返回了无关 Chunk。

原因是不同模型的相似度分布不同：

- Mock 哈希向量的无关文本往往接近 0。
- 真实语义模型即使面对无关文本，也可能得到 0.2～0.4 的正相似度。

所以阈值不是跨模型通用常量。

## 7. 如何诊断分数分布

评测报告增加：

- 每题召回来源数量。
- Top 分数。
- 每条来源的排名、分数和是否命中标准证据。

为了观察完整候选，本次使用实验参数：

```powershell
--similarity-threshold -1
```

这只覆盖本次 CLI 运行，不修改在线生产配置。

诊断结果：

```text
回答题最低正确证据分数：0.5763
拒答题最高无关 Top 分数：0.3602
```

两类数据在当前种子集上存在明显间隔。

## 8. 为什么选择 0.45

`0.45` 位于：

```text
最高无关分数 0.3602
< 0.45
< 最低正确分数 0.5763
```

使用 `0.45` 重新运行：

```text
Recall@3：100% → 100%
MRR：85% → 85%
拒答准确率：71.43% → 100%
```

它保留了全部标准证据，同时过滤了四条无关问题。

但 `0.45` 仍只是“当前候选阈值”，不是永久真理。随着语料、问题和文档类型增加，分数分布会变化，
必须在 30～50 条正式评测集上再次验证。

## 9. 为什么按 Provider 保存独立阈值

旧配置只有：

```text
RAG__SEARCH_SIMILARITY_THRESHOLD=0.2
```

这会让 Mock 和真实模型共享边界。

新配置：

```env
RAG__MOCK_SIMILARITY_THRESHOLD=0.2
RAG__OPENAI_COMPATIBLE_SIMILARITY_THRESHOLD=0.45
```

在线检索流程：

```text
读取知识库 Embedding Provider 快照
→ RagSettings.similarity_threshold_for(provider)
→ 过滤候选 Chunk
→ SearchResponse 返回本次实际阈值
```

显式构造 `KnowledgeSearchService(similarity_threshold=...)` 仍可用于单元测试和离线实验，
但正常 API 不再提前写死全局阈值。

## 10. Mock 与真实基线如何比较

| 指标 | Mock | text-embedding-v4 |
| --- | ---: | ---: |
| Recall@3 | 100% | 100% |
| MRR | 90% | 85% |
| 拒答准确率 | 100% | 100% |
| 平均延迟 | 约 25ms | 约 4084ms |

不能根据这张小表得出“Mock 比真实模型好”：

- 当前问题与原文词语接近，对词面哈希非常有利。
- 语料只有一个短文档，缺少干扰文档。
- 还没有近义改写、指代和跨文档问题。
- 真实模型包含远程网络延迟，Mock 完全本地运行。

这恰好说明下一批评测必须加入真正考验语义能力的问题。

## 11. 为什么基线要脱敏

原始诊断 JSON 含有本地数据库生成的 Chunk UUID。它们：

- 重新入库后会变化。
- 对跨环境比较没有意义。
- 可能暴露内部业务标识。

版本化真实基线只保留：

- case_id。
- 指标。
- 排名。
- 来源数量。
- Top 分数。
- 延迟。

不提交本地 Chunk UUID 和原文正文。

## 12. 延迟与费用如何理解

14 道题每题调用一次远程 Embedding，平均约 4 秒，明显慢于本地 Mock。

当前 Runner 为了稳定解释采用串行执行。后续优化方向：

- 评测 Runner 使用有限并发。
- 查询向量缓存用于重复阈值实验。
- 对同一批向量离线重算多个阈值，避免重复模型费用。
- 记录请求次数、输入 Token 和估算成本。

阈值过滤发生在向量已经生成之后，因此重复尝试不同阈值不应该每次都重新请求远程模型。
这会成为后续评测工具的优化点。

## 13. 关键代码入口

- `backend/app/core/config.py`
  - 环境变量维度转换和 Provider 独立阈值。
- `backend/app/modules/knowledge/service/knowledge_search_service.py`
  - 根据知识库 Provider 动态选择阈值。
- `backend/app/modules/knowledge/evaluation/cli.py`
  - `--similarity-threshold` 实验覆盖。
- `backend/app/modules/knowledge/evaluation/metrics.py`
  - 来源分数和相关性诊断。
- `backend/evaluation/baselines/text-embedding-v4-1024-v1.*`
  - 脱敏真实基线。

## 14. 测试保护什么

- 字符串 `"1024"` 能转换为整数。
- 768 等维度仍被拒绝。
- Mock 默认阈值为 0.2。
- OpenAI-compatible 默认阈值为 0.45。
- 显式阈值可以覆盖默认值。
- 报告包含召回数、Top 分数和来源相关性。
- 真实基线覆盖同一套 14 题。
- 真实基线不保存本地 Chunk UUID。

## 15. 常见错误

| 错误 | 后果 | 当前方案 |
| --- | --- | --- |
| 修改 `.env` 后不重启 Worker | 新入库仍使用旧配置 | 配置变更后重启进程 |
| 真实模型复用 Mock Chunk | 向量空间不一致 | 独立知识库重新入库 |
| 所有模型共享 0.2 阈值 | 真实模型大量误召回 | Provider 独立阈值 |
| 看几个问题拍脑袋改阈值 | 可能提高拒答却损失召回 | 比较正负样例分数间隔 |
| 只提交原始诊断 JSON | 泄露本地标识 | 保存脱敏基线 |
| 把 14 题结果当作生产结论 | 过拟合小样本 | 扩展到 30～50 题重测 |

## 16. 面试表达

> 接入真实 text-embedding-v4 后，我没有沿用 Mock 的相似度阈值。第一次评测发现 Recall 仍是 100%，但四条无证据问题全部误召回。随后给评测报告增加来源分数和相关性诊断，得到最低正确证据 0.5763、最高无关结果 0.3602，在间隔中选择 0.45。重新运行后保持 Recall 和 MRR，同时把拒答准确率从 71.43% 提升到 100%。最终把阈值改为按 Embedding Provider 独立配置，并明确这只是小样本候选值，正式数据扩展后还要重新校准。

## 17. 小练习

用 15～20 分钟增加一个配置测试：

```text
RagSettings().similarity_threshold_for("unknown-provider")
```

应该抛出包含“不支持”字样的 `ValueError`。

答案要点：

1. 不要让未知 Provider 默默回退到 Mock 阈值。
2. 使用 `pytest.raises(ValueError, match="不支持")`。
3. 该测试属于配置单元测试，不需要数据库或 API Key。

## 18. 下一步

下一步扩展正式评测难度：

```text
增加 2～3 份干扰文档
→ 增加近义改写、指代、多文档和难拒答问题
→ 扩充到 30～50 条
→ 重跑 Mock 与真实基线
→ 再决定是否增加混合检索或重排序
```

现有真实 Key 可以继续使用，不需要聊天模型 Key。
