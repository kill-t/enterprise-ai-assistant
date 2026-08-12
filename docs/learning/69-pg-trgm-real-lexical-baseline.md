# 69. PostgreSQL pg_trgm 真实关键词检索基线

## 这一模块解决什么问题

上一模块已经定义了词法检索接口和 RRF 融合算法，但使用的还是内存 Mock。这个模块把接口接到真实 PostgreSQL，让系统能从 Chunk 正文中补充关键词候选。

它主要服务这些向量检索容易漏掉的内容：

- `MinIO`、`Redis`、`HTTP 200` 等技术术语。
- 项目代号、产品型号、人名和日期。
- 用户与资料使用完全相同文字的精确查询。

`pg_trgm` 会把文字拆成连续的三个字符片段，并比较问题与正文中最相似的连续区域。它不是分词器，也不是 BM25，更不理解“牵头人”和“负责人”是同义词。

## 在整体架构中的位置

```text
用户问题
  ↓
PgTrgmLexicalRetriever（统一接口与错误边界）
  ↓
PgTrgmLexicalRepository（权限过滤与 SQL）
  ↓
PostgreSQL pg_trgm + GiST 索引
  ↓
LexicalRetrievalCandidate（完整来源信息）
```

当前线上聊天仍然只走原有向量检索。这个模块先把真实词法通道独立做对，下一模块才会把两路候选交给 RRF。

## 数据如何流动

一次检索请求带着：

- 当前用户 ID。
- 当前会话绑定的知识库 ID。
- 问题正文。
- 最多返回多少条候选。

Repository 在同一条 SQL 中完成：

```text
限制知识库属于当前用户
→ 排除已归档知识库
→ 只读取 READY 文档
→ 使用 pg_trgm 过滤相似正文
→ 按词法距离排序
→ LIMIT 候选数量
```

权限过滤不能等查询结束后再在 Python 中处理，否则不仅浪费数据库工作，还可能让其他用户的资料进入中间结果或日志。

## 迁移增加了什么

迁移 `f4c8a2d6e190` 做两件事：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_document_chunks_content_gist_trgm
ON document_chunks USING gist (content gist_trgm_ops);
```

本地执行命令：

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
```

降级一版可使用：

```powershell
.\.venv\Scripts\alembic.exe downgrade -1
```

降级只删除本模块的索引，不删除 `pg_trgm` 扩展。扩展属于数据库共享能力，未来其他表也可能使用；某个模块回滚时擅自删除它可能破坏其他功能。

## 为什么选 GiST，而不是 GIN

两种索引没有绝对好坏：

- GiST 更适合按 trigram 距离取 Top-K，即“找最相似的前几条”。
- GIN 通常更适合判断大量行是否匹配某个条件。

当前需求是生成一个有顺序的候选榜，所以先选择 GiST。以后如果主要查询变成大范围布尔过滤，应重新用真实数据比较执行计划和延迟。

## 三个关键 pg_trgm 表达式

Repository 使用：

```sql
word_similarity(:query, content)  -- 返回 0 到 1 的展示分数
:query <% content                 -- 使用事务阈值过滤，可利用 trigram 索引
:query <<-> content               -- 返回距离，越小越相关
```

最初如果写成下面这样：

```sql
word_similarity(:query, content) >= 0.1
```

结果虽然正确，但 PostgreSQL 无法把普通函数比较转换成 trigram 索引条件。真实 `EXPLAIN` 检查发现了这个问题，因此改为可索引的 `<%` 操作符。

## 为什么先执行 set_config

`<%` 使用 PostgreSQL 参数 `pg_trgm.word_similarity_threshold`。每次请求先执行：

```sql
SELECT set_config('pg_trgm.word_similarity_threshold', '0.1', true);
```

第三个参数 `true` 表示设置只在当前数据库事务中有效。连接回到连接池后不会把本次阈值污染给其他请求。

这两条 SQL 使用同一个 `AsyncSession`，因此属于同一个事务。项目不会并发共享同一个 `AsyncSession`；如果共享，不同任务修改事务参数就会互相影响。

## Adapter 和 Repository 为什么分开

`PgTrgmLexicalRepository` 只负责数据库：

- 拼 SQL。
- 执行查询。
- 把数据库行映射成候选对象。

`PgTrgmLexicalRetriever` 负责通道规则：

- 校验阈值范围。
- 校验候选是否重复、越界或乱序。
- 把数据库异常转换成不泄露连接信息的 `LexicalRetrievalError`。
- 记录中文告警日志。

以后替换成 BM25、OpenSearch 或 Elasticsearch 时，RRF 和上层业务不需要理解底层 SQL。

## 真实数据库验证结果

迁移后已经验证：

- `pg_trgm 1.6` 和 `vector 0.8.2` 同时存在。
- GiST 索引存在，并能在可索引操作符查询中进入执行计划。
- 原有 51 条 Chunk 没有丢失。
- 已知用户查询 `MinIO` 能返回结果，随机用户查询同一知识库返回 0 条。

使用与向量检索相同的 35 道困难集、Top-K=3、阈值 0.1，结果是：

| 指标 | pg_trgm |
| --- | ---: |
| Recall@3 | 44.00% |
| MRR | 37.33% |
| 整体行为准确率 | 51.43% |
| 困难拒答准确率 | 20.00% |
| 平均数据库检索延迟 | 10.20 ms |
| P95 延迟 | 18.90 ms |

它对 `Redis` 和工具治理等包含明显原词的问题命中很好，但多文档 Recall 为 0，近似干扰 Recall 也为 0。许多拒答问题与资料主题相似，却没有真正答案，因此词法相似度也不能承担拒答判断。

## 如何理解这个结果

结论不是“pg_trgm 不好”，而是它的职责不能放错：

```text
真实向量：负责同义表达和语义召回
pg_trgm：补充精确术语和原词命中
RRF：融合两路名次
EvidenceSelector：判断证据是否真正足以回答
```

只有把两路候选融合后再跑同一困难集，才能判断混合检索是否真正提升。BM25 仍是合理候选，但现在不同时引入第二套关键词实现；如果 RRF 后指标仍不足，再用相同数据实现和比较 BM25。

## 常见错误

1. 只看 SQL 返回正确，不用 `EXPLAIN` 检查索引是否生效。
2. 在 Python 中过滤用户权限，让越权内容先进入候选集。
3. 把距离当作分数；距离越小越好，统一候选分数约定却是越大越好。
4. 使用连接级 `SET` 后不恢复，造成连接池中的下一次请求继承阈值。
5. 把低延迟误认为高质量，或者把“主题相似”误认为“足以回答”。
6. 直接把向量分数和词法分数相加；两种分数含义不同，不能裸加。

## 面试时可以怎么讲

> 我先用 PostgreSQL pg_trgm 建立低成本词法基线，并通过用户、知识库和 READY 状态的 SQL 过滤保证数据隔离。开始时普通函数阈值查询没有利用索引，我通过 EXPLAIN 发现后改为 `<%` 可索引操作符和事务级阈值，再用 GiST 距离排序。35 道困难集表明它延迟约 10 毫秒，但 Recall@3 只有 44%，所以我没有用它替换向量检索，而是把它定位为精确术语候选源，下一步使用 RRF 做混合召回，并由独立证据选择层负责拒答。

## 15–30 分钟小练习

请回答下面三个问题，不需要改代码：

1. 为什么 `<%` 前要设置事务级阈值，而不能设置成永久连接参数？
2. 为什么 SQL 已经返回正确结果，仍然需要查看 `EXPLAIN`？
3. 如果用户问“谁在牵头蓝鲸项目”，资料写的是“负责人是林晓”，你预计 pg_trgm 和真实向量谁更可能找到它？为什么？

答案要点：请求之间不能互相污染；正确性不等于执行效率；同义改写通常由语义向量承担，词法通道只负责补充原词信号。
