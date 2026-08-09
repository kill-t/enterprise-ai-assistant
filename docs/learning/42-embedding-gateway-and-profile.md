# Embedding 网关、批处理与配置快照

## 1. 这一小步解决什么问题

上一步已经能把 PDF、TXT、Markdown 解析成 `ChunkDraft`，但数据库里的
`document_chunks.embedding` 是非空的 `vector(1024)`。因此 Chunk 不能直接写入数据库，
必须先得到合法向量。

本模块建立如下内部链路：

```text
有序文本列表
→ EmbeddingService 校验并拆批
→ BaseEmbeddingProvider
   ├─ MockEmbeddingProvider
   └─ OpenAICompatibleEmbeddingProvider
→ 校验模型、数量、顺序、维度和数值
→ EmbeddingResult
```

本模块还不下载 MinIO 文件、不运行 Celery Worker、不写 Chunk 表，也不提供检索 API。
下一模块的 Worker 会组合 Parser、Chunker 和这里的 EmbeddingService。

## 2. Embedding 可以怎样浅显理解

Embedding 把一段文字转换成一串固定长度的数字：

```text
“项目会议安排” → [0.01, -0.08, ..., 0.03]  共 1024 个数
```

真实语义模型会尽量让意思相近的文本在向量空间中更接近。以后用户提出问题时，系统也把
问题转换成同维向量，再使用余弦距离寻找最相近的文档 Chunk。

下面三项必须一致：

- 文档向量使用的 Provider。
- 文档向量使用的模型。
- 文档向量的维度。

如果旧文档由模型 A 生成，新查询由模型 B 生成，即使都是 1024 维，这些数字也不属于同一
坐标空间，距离没有可靠意义。

## 3. 为什么需要 Profile 配置快照

`EmbeddingProfile` 保存：

```text
provider
model
dimension
```

新建知识库时，`KnowledgeBaseService` 会把当前默认 Profile 写入
`knowledge_bases`。以后全局配置从 Mock 切换到真实模型，旧知识库仍明确标记为
`mock / mock-hash-1024 / 1024`，不会悄悄使用新模型查询旧向量。

这里要区分两个概念：

- 全局配置：现在新建知识库默认用什么。
- 知识库快照：这个知识库已经使用什么，后续入库和检索必须继续匹配它。

修改全局配置不会自动重算旧知识库。未来若允许更换模型，必须设计“整库重新入库”，而不是
直接修改三个字段。

## 4. 为什么 Provider 和 Service 分开

`BaseEmbeddingProvider` 只定义一个最小能力：

```python
async def embed(request: EmbeddingRequest) -> EmbeddingResult
```

Provider 负责厂商协议，例如 API 地址、SDK 参数和响应字段。`EmbeddingService` 负责项目
自己的规则：

- 拒绝空批次和空白文本。
- 按配置顺序拆批。
- 保持输入与向量一一对应。
- 检查模型和维度。
- 检查响应数量。
- 拒绝 `NaN` 和无穷大。

这样业务规则不会散落在阿里云、OpenAI 或 Mock 适配器中。以后替换厂商，Worker 仍然只
调用 `EmbeddingService.embed_texts()`。

## 5. 批处理为什么必须保持顺序

假设 Worker 产生三个 Chunk：

```text
Chunk 0 → 文本 A
Chunk 1 → 文本 B
Chunk 2 → 文本 C
```

Embedding 返回的第 0、1、2 个向量必须分别属于 A、B、C。顺序错位不会触发数据库错误，
但检索会返回“内容是 A、向量却来自 B”的隐蔽脏数据。

`EmbeddingService` 顺序拆批并顺序汇总。OpenAI 兼容适配器还会按响应中的 `index`
重新排序，并要求索引必须从 0 连续到批次末尾。

默认批次大小是 10。阿里云官方文档当前说明 `text-embedding-v4` 的 OpenAI 兼容同步接口
单批最多 10 行，并支持 `dimensions=1024`：
[OpenAI Embedding 接口兼容](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai/)。

第一版按顺序调用多个批次，没有并行轰炸接口。这样更容易控制限流、错误定位和任务重试。

## 6. Mock 为什么稳定，但不是真实语义模型

`MockEmbeddingProvider` 使用以下过程：

```text
英文词 / 中文单字 / 中文二元词
→ SHA-256 稳定哈希
→ 映射到 1024 个向量槽位
→ L2 归一化
```

不能使用 Python 内置 `hash()`，因为它默认会在不同进程间随机化，Worker 重启后同一文本
可能产生不同向量。SHA-256 对相同输入始终产生相同结果，测试可复现。

Mock 能识别“共享了相同词语”，但不理解同义词。例如“开会”和“召开会议”可能不会像真实
语义模型那样接近。因此它适合：

- 自动化测试。
- 无 API Key 的本地演示。
- 验证上传、入库、检索和引用的数据链路。

它不适合证明真实 RAG 的语义召回质量。项目 README 已明确标注这一限制。

## 7. 真实 Provider 怎样调用

`OpenAICompatibleEmbeddingProvider` 通过异步 OpenAI SDK 发送：

```text
model=text-embedding-v4
input=[有序文本列表]
dimensions=1024
encoding_format=float
```

默认地址保留阿里云仍支持的旧兼容域名，便于直接配置；官方现在更建议在本机 `.env` 中换成
业务空间专属域名。真实密钥只写入未提交的 `.env`：

```text
EMBEDDING__PROVIDER=openai_compatible
EMBEDDING__API_KEY=本机真实密钥
EMBEDDING__BASE_URL=你的业务空间兼容地址
EMBEDDING__MODEL=text-embedding-v4
```

模型调用失败时，服务端日志记录 Provider、模型和异常类型；向 Worker 抛出的
`EmbeddingError` 只包含稳定错误码和安全中文提示，不携带第三方响应正文或密钥。

## 8. 关键代码入口与调用链

- 配置：`backend/app/core/config.py` 中的 `EmbeddingSettings`。
- 数据契约：`backend/app/infrastructure/embedding/schemas.py`。
- 抽象接口：`backend/app/infrastructure/embedding/base.py`。
- Mock：`backend/app/infrastructure/embedding/mock_provider.py`。
- 真实适配器：`backend/app/infrastructure/embedding/openai_compatible_provider.py`。
- 批处理与校验：`backend/app/infrastructure/embedding/service.py`。
- 工厂：`backend/app/infrastructure/embedding/provider_factory.py`。
- 知识库快照：`backend/app/modules/knowledge/service/knowledge_base_service.py`。

当前真实调用链是：

```text
创建知识库
→ KnowledgeBaseService
→ EmbeddingProviderFactory.default_profile()
→ KnowledgeBaseRepository
→ PostgreSQL 保存配置快照
```

Worker 完成后会增加：

```text
ChunkDraft.content 列表
→ EmbeddingProviderFactory.create_service(知识库快照)
→ EmbeddingService.embed_texts()
→ Provider.embed()
→ EmbeddingResult.vectors
→ 单个数据库事务写入 Chunk 和任务终态
```

## 9. 测试保护了什么

`tests/test_embedding_gateway.py` 覆盖：

- 空输入和空白文本不会调用 Provider。
- Mock 在多次实例化后仍产生相同向量。
- Mock 向量固定维度、数值有限并完成归一化。
- 共享词语比无关文本更接近。
- 五条输入按 `2 + 2 + 1` 拆批后顺序不变。
- 模型、数量、维度和非有限数值异常被拒绝。
- OpenAI 兼容请求参数正确，响应按 `index` 还原。
- SDK 异常转换为安全错误。
- 默认工厂不消耗真实额度。
- 配置不能改成与数据库列不一致的 768 维。

`tests/test_knowledge_base_crud.py` 还保护“注入新 Profile 后，新知识库保存新快照”。

## 10. 常见错误与调试顺序

### 向量维度不一致

先检查：

```text
knowledge_bases.embedding_dimension
document_chunks.embedding_model
EMBEDDING__DIMENSION
模型接口实际返回的向量长度
```

不要通过截断或补零把不同维度硬凑成 1024。

### 输入有 N 条但响应少于 N 条

这属于协议异常，不能只保存已有部分。Worker 后续应让本次入库失败或重试，避免 Chunk 与
向量错位。

### Mock 检索效果不好

先确认问题和原文是否共享明显词语。Mock 不理解真正语义；要评估语义召回，应配置真实
Provider，并使用固定评测集比较 Recall@K、MRR、延迟和费用。

## 11. 面试时可以怎样表达

> 我没有把 Embedding SDK 直接写进 Celery Task，而是建立了厂商无关的异步 Provider
> 契约。Service 负责顺序拆批和协议校验，知识库冻结 Provider、模型与维度，避免配置切换
> 后混用向量空间。默认 Mock 使用稳定哈希完成无额度端到端演示，但文档明确说明它不代表
> 真实语义效果。真实适配器使用 OpenAI 兼容接口，并对数量、顺序、维度和非有限数值做
> 防御性检查。

## 12. 小练习（15–30 分钟）

给 `EmbeddingService` 补充“Provider 返回负数 `input_tokens` 时拒绝响应”的规则：

1. 先在 `tests/test_embedding_gateway.py` 写一个失败测试。
2. 期望错误码使用 `PROVIDER_RESPONSE_INVALID`。
3. 再在 `_validate_result()` 中增加最小判断。
4. 运行目标测试，确认从红灯变绿灯。

答案要点：Token 用量是监控和成本字段，不能接受明显不可能的负数；这个判断属于统一协议
规则，应放在 `EmbeddingService`，不应分别复制到 Mock 和真实 Provider。
