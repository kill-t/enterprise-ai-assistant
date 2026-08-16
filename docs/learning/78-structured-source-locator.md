# 模块 78：跨文档格式的结构化来源位置

## 1. 本模块解决什么问题

旧 RAG 只支持 PDF、TXT 和 Markdown，因此 Chunk 与引用使用三个固定字段就基本够用：

```text
heading_path
page_start
page_end
```

准备增加 Word、Excel 和 OCR 后，这三个字段无法准确表达全部位置：

- Word 更适合使用标题路径、段落或表格编号。
- Excel 必须说明工作表和单元格范围。
- OCR 需要页码、图片区域、识别置信度和引擎。

如果把 `Sheet1!A2:F12` 塞进 `heading_path`，代码虽然暂时能运行，但字段语义已经错误，前端、Prompt
和审计代码以后都会堆积格式判断。本模块先建立统一位置协议，再开发具体 Parser。

## 2. 最终数据结构

每条 Chunk 和引用同时保存：

```text
source_type    → 原文件格式
source_locator → 该 Chunk 在原文件中的结构化位置
```

`source_type` 当前允许：

```text
pdf / text / markdown / docx / xlsx / image
```

`source_locator` 使用 `kind` 作为判别字段：

```json
{"kind":"page","page_start":2,"page_end":3,"heading_path":[]}
{"kind":"section","heading_path":["资助政策","申请条件"]}
{"kind":"sheet_range","sheet_name":"资助标准","cell_range":"A2:F12"}
{"kind":"ocr_region","page_number":4,"bounding_box":{},"confidence":0.91}
{"kind":"document"}
```

## 3. 为什么使用 JSONB

如果全部拆成普通列，表中会出现大量只适用于某种格式的可空字段，例如 `sheet_name`、`cell_range`、
`ocr_x_min`、`ocr_engine`。每增加一种文档格式都要继续加列。

JSONB 的优势是：

- 不同位置形状可以保存在同一列。
- 以后增加 PPT 页、邮件附件等类型时更容易扩展。
- PostgreSQL 仍能检查它必须是对象并包含 `kind`。

JSONB 的代价是数据库难以表达所有组合规则。因此采用双层校验：

```text
PostgreSQL：非空、必须是 JSON 对象、必须有 kind
Pydantic：页码顺序、单元格格式、坐标范围、联合类型和额外字段
```

当前不按位置执行数据库搜索，所以不为 JSONB 创建 GIN 索引。索引只会增加写入成本而没有真实查询
收益。

## 4. 为什么保留旧页码和标题字段

本模块没有立即删除 `heading_path/page_start/page_end`，原因是：

- 已有 PDF 引用和测试仍使用它们。
- 旧前端或导出逻辑可能仍然读取这些字段。
- 新旧字段并行一段时间，可以降低一次大迁移的风险。

新字段是未来格式的事实位置；旧字段属于兼容视图。等 Word、Excel、OCR 全部稳定后，再单独评估是否
删除旧字段，不能在本模块顺手删除。

## 5. 真实调用链

```text
Parser 输出 ParsedSection
→ TokenAwareChunker 生成 ChunkDraft.source_locator
→ DocumentIndexRepository 推断 source_type 并序列化 JSONB
→ document_chunks
→ pgvector / pg_trgm Repository 读取并校验 Locator
→ 向量候选、关键词候选和 RRF 保留同一位置
→ EvidenceSelector 与 Grounded Prompt 收到位置
→ ConversationService 发送 SSE sources
→ MessageCitationRepository 保存不可变位置快照
→ 历史 API / Vue 引用卡片恢复位置
```

这里最容易犯的错误是只修改 ORM 和响应 Schema，却忘记中间某个 dataclass。代码不会总是立刻报错，
但 Excel 单元格可能在 RRF 或证据选择阶段悄悄丢失。本模块因此逐层显式复制字段，并让两路混合检索
比较位置是否一致。

## 6. 关键代码入口

### `app/modules/knowledge/source_locator.py`

这是统一位置协议：

- `PageSourceLocator`：连续页码。
- `SectionSourceLocator`：标题路径和可选内容块。
- `SheetRangeSourceLocator`：工作表、数据范围和表头范围。
- `OcrRegionSourceLocator`：归一化坐标、页码、置信度和引擎。
- `DocumentSourceLocator`：没有内部结构的整篇文本。
- `parse_source_locator`：把未知 JSON 严格校验成联合类型。
- `serialize_source_locator`：把模型转回可存入 JSONB 的普通对象。
- `infer_source_type`：根据受支持扩展名确定原文件格式。

### Chunk 与引用实体

`document_chunks` 保存当前位置，因为检索结果直接来自 Chunk。

`message_citations` 保存 `source_type_snapshot/source_locator_snapshot`。它们是回答生成时的不可变快照；
即使用户删除原文档，历史回答仍可以说明当时引用的是哪个工作表或页面。

### Vue 引用组件

前端不再统一显示“页码未知”，而是根据 `kind` 展示：

```text
第 3–5 页
资助政策 / 申请条件
资助标准!A2:F12
第 4 页 · OCR 置信度 91%
整篇文档
```

SSE Decoder 也会校验结构化位置，错误页码、错误 Excel 地址或越界 OCR 坐标不会进入 Pinia。

## 7. 数据库迁移怎样保护旧数据

迁移不能直接添加非空列，否则已有 Chunk 会因为没有值而失败。正确顺序是：

```text
添加可空列
→ 根据文件名、旧页码和旧标题回填
→ 把列改为 NOT NULL
→ 添加数据库 CheckConstraint
```

迁移回滚只删除新约束和新字段；旧页码与标题一直存在，因此回滚不会丢掉旧版可识别的位置。

执行命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

检查当前版本：

```powershell
.\.venv\Scripts\python.exe -m alembic current
```

## 8. 事务边界

文档入库成功时，下面操作仍在同一个 Worker 最终事务中：

```text
删除旧 Chunk
→ 写入包含新 Locator 的全部 Chunk
→ 文档切换 READY
→ 入库任务切换 SUCCEEDED
→ commit
```

任何 Locator 序列化或数据库写入失败都会回滚全部暂存结果，不会产生只有部分 Chunk 带新位置的索引。

回答完成时，助手正文和引用位置快照仍在同一数据库事务中提交。Redis 只在提交成功后回填，不承担引用
事实。

## 9. 正常、失败和兼容路径

### 正常路径

当前 PDF Chunk 自动生成 `page`，Markdown 标题自动生成 `section`，纯 TXT 生成 `document`。

### 非法位置

应用层会拒绝：

- `page_start=3, page_end=2`。
- 空 Word 标题路径。
- `错误范围` 这样的 Excel 地址。
- 超出 0～1 的 OCR 坐标或置信度。
- `block_kind` 和 `block_index` 只出现一个。

### 旧数据升级

迁移从旧字段回填结构化 JSON；旧 API 字段继续返回，不要求重新上传现有 PDF。

## 10. 测试保护什么

- 联合类型能够识别五种位置。
- 矛盾页码、空标题、非法单元格和 OCR 越界被拒绝。
- Chunker 同时生成兼容旧字段和新 Locator。
- 两张表使用非空 JSONB 和数据库约束。
- 引用 Repository 冻结位置快照但不自行提交事务。
- 迁移先回填再收紧非空，并且可以降级。
- 向量、关键词、混合检索和 LLM 证据选择不会丢位置。
- SSE 拒绝非法位置。
- Vue 正确显示 Excel 范围和 OCR 置信度。
- 全部旧 PDF、聊天和引用测试继续通过。

## 11. 设计取舍

### 只保存一个自由字典

扩展最快，但拼错 `cell_range` 或写入倒置坐标也不会报错，不适合可审计系统。

### 给每种文档创建独立 Chunk 表

数据库约束最强，但检索需要 UNION 多张表，Embedding、权限和引用逻辑会重复，当前没有收益。

### 立即删除旧字段

结构更干净，但会把兼容迁移、前端升级和多格式支持绑在一次高风险修改中，因此暂不采用。

## 12. 面试表达

> 我没有把 Excel 地址伪装成 PDF 页码，而是设计了 source_type 加带判别字段的 JSONB Locator。数据库
> 负责基本形状约束，Pydantic 判别联合负责不同格式的严格业务校验。结构化位置沿向量、关键词、RRF、
> SSE 和引用快照完整传递；旧 PDF 字段暂时双写兼容，迁移先回填再收紧非空约束。

## 13. 小练习（约 15～30 分钟）

给 `SheetRangeSourceLocator` 增加一个失败测试：

```text
cell_range = A0:F12
```

说明为什么第 0 行不合法，并观察现有正则是否已经拒绝它。然后再增加一个小写输入：

```text
cell_range = a2:f12
```

答案要点：

- Excel 行号从 1 开始，所以 `A0` 必须失败。
- 输入校验前会统一转为大写，因此 `a2:f12` 应保存为 `A2:F12`。
- 这属于输入规范化，不应该让数据库里同时存在大小写不同但语义相同的地址。
