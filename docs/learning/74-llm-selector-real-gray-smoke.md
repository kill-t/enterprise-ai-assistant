# 74. LLM 证据选择器真实灰度联调

## 这一模块解决什么问题

前一个模块已经让搜索 API 与会话 RAG 能够通过配置切换到 `LLMEvidenceSelector`，但单元测试中的 Provider 都是 Mock。配置正确不等于真实网络链路一定正确，本模块用少量真实调用验证：

```text
HTTP 请求
→ JWT 用户身份
→ KnowledgeSearchService 共享工厂
→ text-embedding-v4 生成查询向量
→ pgvector 宽召回候选
→ DeepSeek V4 Flash 判断证据充分性
→ 搜索来源或正常拒答
```

随后再验证相同检索能力进入会话时，能否经过 SSE 把来源送给前端，并在刷新历史后恢复引用。

本模块没有新增业务代码、数据表和迁移。它属于真实集成验收，不用更多抽象掩盖现有问题。

## 为什么使用进程级环境变量

本地 `.env` 继续保持：

```text
RAG Selector：threshold
聊天 Provider：mock
```

测试后端只在当前进程临时覆盖：

```powershell
$env:RAG__EVIDENCE_SELECTOR = "llm"
$env:RAG__LLM_EVIDENCE_SELECTOR_MODEL = "deepseek-v4-flash"
$env:RAG__LLM_EVIDENCE_SELECTOR_THINKING_ENABLED = "false"
python -m app.local_server
```

进程关闭后变量随进程消失，不修改 `.env`，也不会让明天的普通本地演示意外产生模型费用。

这里没有把聊天 Provider 一起切换为 DeepSeek。当前验收只改变一个变量：证据选择器。聊天最终正文继续由 Mock Provider 流式生成，便于明确区分：

- Embedding 是否成功；
- Selector 是否正确选证据或拒答；
- SSE 与引用持久化是否正确；
- 最终聊天模型的语言质量。

如果四项同时切换，失败时很难快速判断是哪一层造成的。

## 第一次：知识搜索 API

使用已有真实 Embedding 困难集知识库执行两个受控样例，`top_k=3`。

| 样例 | 标准行为 | HTTP | 最终来源 | 总延迟 |
| --- | --- | --- | ---: | ---: |
| `bw-current-identity-owner` | 回答 | 200 | 1 条 | 5228.29 ms |
| `blue-whale-budget` | 拒答 | 200 | 0 条 | 4545.92 ms |

回答题最终选择：

```text
blue-whale-project-charter.md / chunk 1 / score 0.7507
```

拒答题虽然从向量层宽召回了 12 条主题相关候选，但 Flash 返回：

```text
answerable = false
reason = missing_specific_fact
selected = 0
```

这正是两阶段检索要解决的问题：资料谈到了蓝鲸项目，不代表资料包含采购预算这个具体事实。

## 第二次：会话 SSE 与引用恢复

测试创建一条临时会话，绑定同一个知识库，然后发送可回答问题：

```text
POST 创建会话
→ PUT 原子绑定知识库
→ POST 流式发送问题
→ GET 刷新消息历史
→ DELETE 清理临时会话
```

真实结果：

| 检查项 | 结果 |
| --- | --- |
| 创建会话 | 201 |
| 绑定知识库 | 200 |
| SSE 请求 | 200 |
| 事件顺序 | `started → sources → delta × 4 → completed` |
| `sources` 数量 | 1 |
| 完成状态 | `completed` |
| 刷新后的消息数 | 2 |
| 刷新后的引用数 | 1 |
| 临时会话清理 | 204 |

SSE 总延迟为 5805.68 ms。历史引用仍指向 `blue-whale-project-charter.md`，说明来源不只存在于内存事件中，而是已经随助手消息持久化到 PostgreSQL。

## 数据库事务在哪里

本次链路包含不同性质的操作：

```text
Embedding、向量检索、Selector
→ 生成准备阶段，只读知识数据

用户消息 + 助手占位
→ 一个数据库事务提交

Mock 流式完成 + 引用快照
→ 更新助手消息并提交引用
```

Selector 得出“无充分证据”不是系统异常，而是正常业务结果，所以搜索接口返回 200 和空来源；会话路径会保存一条正常完成的拒答消息。Selector 超时或协议错误才属于系统失败，不能伪装成空来源。

## 调试中遇到的典型错误

第一次临时命令直接把中文问题放进 Windows 的 `python -c` 参数，命令行编码使正文变成乱码。请求虽然返回了 200，但这种结果不能作为有效验收，因为服务端实际收到的问题已经改变。

修复方式不是忽略乱码，而是从项目内 UTF-8 JSONL 评测集按 `case_id` 读取问题：

```text
命令行只传 ASCII case_id
→ Python 使用 UTF-8 读取数据集
→ HTTPX 以 JSON/UTF-8 发送真实问题
```

另一个细节是临时 HTTP 客户端设置 `trust_env=False`，避免本机代理环境变量错误接管 `127.0.0.1` 请求。生产客户端是否遵循代理应由部署配置决定，不能照搬本地 smoke 的设置。

## 测试完成后的状态检查

测试结束后执行了四项清理和确认：

- 临时会话已经删除，数据库中同名记录为 0。
- 临时后端已经关闭，8000 端口没有监听进程。
- Docker 中 PostgreSQL、Redis、MinIO 保持运行，数据卷没有改动。
- 新进程重新读取配置时仍是 `threshold + mock`。

本次测试没有修改或打印 API Key，也没有运行 `docker compose down -v`。

## 这次属于哪种测试

| 类型 | 本模块是否执行 | 保护的行为 |
| --- | --- | --- |
| 单元测试 | 前一模块已覆盖 | 工厂选择、参数传递、异常映射 |
| 集成测试 | 是 | FastAPI、PostgreSQL、pgvector、真实远程 Provider |
| 协议测试 | 是 | 搜索 JSON 与 SSE 事件顺序 |
| 端到端测试 | 部分 | 后端链路完整，暂未由浏览器点击前端 |

所以“接口返回 200”不是唯一成功标准。本次还检查了选中的文件、正确拒答、SSE 顺序、完成状态、历史引用和清理结果。

## 关键代码入口

- `backend/app/modules/knowledge/service/knowledge_search_factory.py`：搜索 API 和聊天共同使用的组装入口。
- `backend/app/modules/knowledge/retrieval/llm_evidence_selector.py`：Flash 证据充分性判断。
- `backend/app/modules/knowledge/service/knowledge_search_service.py`：候选检索与最终来源返回。
- `backend/app/modules/conversation/service/conversation_service.py`：Grounded Chat、SSE 和引用提交。
- `backend/app/modules/conversation/api/conversation_router.py`：HTTP SSE 入口。
- `backend/evaluation/datasets/rag-v2-challenge.jsonl`：可复现的受控问题来源。

## 面试时可以怎么讲

> 我在离线评测和配置接入之后，又做了小流量真实灰度。测试进程临时启用 DeepSeek V4 Flash EvidenceSelector，本地默认仍保持 threshold 和 Mock，避免误消费。搜索 API 同时验证了正确来源与困难拒答；会话链路验证了 started、sources、delta、completed 的 SSE 顺序，以及刷新页面后引用仍能从 PostgreSQL 恢复。测试结束后清理临时会话、关闭后端并确认默认配置没有漂移。

## 15–30 分钟小练习

请沿下面的数据流，用自己的话回答每一层为什么不能省略：

```text
问题
→ Embedding
→ pgvector 宽召回 12 条
→ Flash 选择 1 条或 0 条
→ SSE sources
→ PostgreSQL 引用快照
```

再回答两个问题：

1. 为什么预算题返回 200 和空来源是成功，而 DeepSeek 超时返回空来源是错误？
2. 为什么收到 `sources` 事件仍不足以证明引用已可靠保存，还必须重新读取历史消息？

答案要点：业务拒答表示系统完成了判断，远程超时表示判断没有完成；SSE 是瞬时传输，刷新历史仍有引用才能证明 PostgreSQL 快照已经持久化。
