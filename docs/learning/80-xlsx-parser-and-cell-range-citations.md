# 模块 80：Excel XLSX 解析与单元格范围引用

## 1. 这个模块解决什么业务问题

企业知识经常以名单、费用标准、项目进度和统计表保存在 Excel 中。把整份工作簿直接拼成一个字符串
会出现三个问题：

- 模型不知道某个数字属于哪一列。
- 大工作表超过上下文预算。
- 回答只能说“来自某 Excel”，不能回到具体工作表和单元格。

本模块打通：

```text
Vue 上传 .xlsx
→ DocumentService 校验扩展名和 MIME
→ MinIO 保存原始文件
→ 持久化 Worker 下载并校验哈希
→ XlsxParser 只读解析可见工作表
→ 表头感知的完整行预分块
→ TokenAwareChunker 执行最终模型预算
→ Mock/真实 Embedding
→ PostgreSQL Chunk 与 pgvector
→ 检索、SSE、引用快照
→ Vue 显示“资助标准!A2:F12”
```

它继续复用同一套 RAG 入库、检索和引用链，没有建立 Excel 专用向量库。

## 2. XLSX 与 DOCX 的共同点

`.xlsx` 和 `.docx` 都是 OOXML ZIP 容器。两者都可能包含大量压缩条目、异常解压大小、宏声明或
损坏 XML。因此新增 `office_archive.py` 作为共用安全边界：

```text
具体 Parser 定义必要部件和禁止目录
→ OfficeArchiveValidator 检查 ZIP
→ Parser 把内部错误转换成 DOCX/XLSX 稳定错误码
```

这样宏和压缩炸弹策略只维护一份。检查过程不把上传内容解压到磁盘，也不执行公式或宏。

## 3. 为什么使用 openpyxl 只读模式

普通模式会为大量单元格创建完整 Python 对象，大工作簿可能消耗远高于原文件大小的内存。当前固定
使用 `read_only=True`，迭代单元格后显式关闭 Workbook。

同时打开两个只读视图：

```text
data_only=False → 读取原始公式
data_only=True  → 读取 Excel 上次保存的公式缓存结果
```

公式存在缓存结果时优先使用缓存；没有缓存时保留 `=B2*2` 这样的公式文本。系统不会执行公式，原因
是 openpyxl 不是计算引擎，而且服务端执行不可信公式会引入安全和结果一致性问题。

`keep_links=False` 防止 openpyxl 加载外部工作簿缓存；第一版还会拒绝 XLSX 中的外部工作簿链接部件。

## 4. 表格区域怎样识别

Parser 按下面的结构规则处理每个可见工作表：

```text
整行空白       → 分隔上下两个区域
整列空白       → 分隔左右并排的表格
区域第一行     → 默认表头
单值行 + 多值行 → 识别为标题行 + 表头行
隐藏工作表     → 不自动进入知识库
```

第一版只处理可见工作表，是为了避免把内部计算表、辅助映射或用户主动隐藏的数据意外加入检索结果。
如果以后需要隐藏表，应该在上传界面让用户明确选择，而不是静默改变默认安全边界。

## 5. 为什么 Parser 先按完整行预分块

如果 Parser 输出整张表，再由通用 Chunker 从字符串中间切开，可能出现：

```text
Chunk 1：只有表头和前半行
Chunk 2：从某个单元格中间开始
```

这会破坏表格语义，也无法给第二个 Chunk 生成准确单元格范围。因此 Parser 先做结构预分块：

- 每块最多 20 个完整数据行。
- 使用“每三个 UTF-8 字节约一个 Token”的保守估算，将块控制在约 520 Token。
- 每个块重复工作表名和表头。
- 最终仍由 `TokenAwareChunker` 使用知识库模型对应的 TokenCounter 校验 600 Token 上限。

如果单个单元格本身超大，最终 Chunker 仍可能拆分它；引用范围保持为包含这段内容的原始数据区域，
不会伪造更小范围。

## 6. 结构化引用怎样保存

例如某块包含第 2～12 行数据，表头位于第 1 行：

```json
{
  "kind": "sheet_range",
  "sheet_name": "资助标准",
  "cell_range": "A2:F12",
  "header_range": "A1:F1"
}
```

- `cell_range` 只描述该块的真实数据行。
- `header_range` 单独描述重复加入 Prompt 的表头来源。
- 前端主要显示 `资助标准!A2:F12`。
- 引用快照保存同一 Locator，文档删除后历史回答仍可审计。

范围协议现在还会拒绝：

- 倒置范围 `B3:A2`。
- 超过 Excel 最大列 XFD 的 `XFE1:XFE2`。
- 超过第 1,048,576 行的地址。

## 7. 关键代码入口

### `processing/xlsx_parser.py`

- `parse()`：安全检查、打开公式和缓存值两个只读 Workbook、关闭资源。
- `_parse_workbook()`：限制工作表、行、列和非空单元格总量。
- `_read_sheet_rows()`：同步读取公式与缓存结果并规范化类型。
- `_build_sheet_sections()`：按空行和空列发现逻辑表格。
- `_build_table_sections()`：识别标题/表头并按完整行预分块。
- `_render_table()`：生成每块自包含的 Markdown 表格。

### `processing/office_archive.py`

DOCX 与 XLSX 共用的 OOXML 容器安全检查，包括：

- 必要包部件。
- 条目数量。
- 单条和总解压大小。
- 异常压缩率。
- 加密标记。
- 宏载荷与宏启用声明。
- 由具体 Parser 配置的禁止目录。

### `source_locator.py`

`SheetRangeSourceLocator` 负责 A1 地址格式、范围顺序和 XLSX 最大行列边界。Parser 负责产生位置，
Chunker 只保留位置；Repository、混合检索、SSE 和引用快照继续原样传递。

## 8. 值类型怎样转换

```text
整数/小数 → 稳定数字文本，不把 5000 变成 5000.0
布尔值   → TRUE / FALSE
日期     → 2026-08-22
日期时间 → 2026-08-22 14:30:00
公式     → 缓存值，或原始 =SUM(...)
换行     → Markdown 单元格内的 <br>
竖线     → \|，防止错误拆列
```

数字最终会进入文本 Embedding，但规范化前保留原始 Python 类型判断，避免把日期、布尔值和公式混为
普通字符串后再猜测。

## 9. 正常、失败与事务边界

### 正常路径

解析和 Embedding 位于数据库事务外。全部 Chunk、Locator 和向量准备好后，Worker 才在一个事务内：

```text
删除旧 Chunk
→ 写入全部 Excel Chunk 与向量
→ 文档改为 READY
→ 任务改为 SUCCEEDED
→ commit
```

### 失败路径

- 损坏或伪造 XLSX：`XLSX_INVALID`。
- 可见工作表都为空：`NO_EXTRACTABLE_TEXT`。
- 工作表、行、列或非空单元格超限：`XLSX_LIMIT_EXCEEDED`。
- 宏、异常 ZIP、外部工作簿链接：`OFFICE_ARCHIVE_UNSAFE`。
- MinIO、Embedding 等临时故障：继续使用现有 Worker 重试和恢复机制。

Parser 失败不会写入半套 Chunk。PostgreSQL 仍是文档状态、任务、Chunk、向量和引用的唯一事实来源。

## 10. 当前明确限制

- 不支持旧二进制 `.xls`。
- 不把 CSV 当成 Excel；CSV 的编码、分隔符和 Schema 要单独设计。
- 不解析图表、图片、数据透视表视觉结果和条件格式语义。
- 不执行或重新计算公式，只读取缓存结果或保留公式文本。
- 保留数字真实值，但第一版不完整还原货币、百分比等所有自定义显示格式。
- 不自动索引隐藏工作表。
- 左右表格必须由至少一整列空白分开；复杂仪表盘布局以后再评估。
- 单个超大单元格可能被最终 Token Chunker 拆开。

这些限制不会影响第一版最常见的名单、标准、统计表和进度表。

## 11. 测试保护什么

- 数字、日期、布尔值和无缓存公式正确转换。
- 表头重复、完整行分块和 `cell_range/header_range` 准确。
- 空行与空列能够拆开多个逻辑表格。
- 隐藏表不会进入知识库。
- 损坏、外部链接和资源超限得到稳定错误。
- 范围倒置和超出 XLSX 边界被 Pydantic 拒绝。
- 上传白名单、Parser Registry 和 Vue 文件选择支持 `.xlsx`。
- 真实 `DocumentIngestionProcessor` 使用 Mock Embedding 生成 1024 维向量，无需 API Key。
- 全量旧 PDF、DOCX、检索、聊天和前端测试继续通过。

## 12. 面试表达

> 我没有把 Excel 整表直接转成一段文本，而是用 openpyxl 只读模式识别可见工作表中的连续数据区域，
> 按空行和空列拆分表格，再按完整行与保守 Token 预算预分块。每块重复表头并保存 sheet_name、
> cell_range 和 header_range。公式优先使用缓存结果，没有缓存时保留公式但不执行。DOCX 与 XLSX 共用
> OOXML ZIP 安全检查，最终 Chunk、向量、任务状态和引用仍由原有事务与检索链管理。

## 13. 小练习（约 15～30 分钟）

在 `test_xlsx_parser.py` 中增加一个单元格值：

```text
材料|说明
第二行
```

验证 Markdown 输出中：

```text
材料\|说明<br>第二行
```

答案要点：

- `|` 是 Markdown 表格列分隔符，必须转义。
- 单元格内部换行不能变成新的表格行，因此转换为 `<br>`。
- 这是 Parser Adapter 的格式转换规则，不属于 Repository 或前端业务逻辑。
- 测试不需要 PostgreSQL、MinIO 或模型密钥。

## 14. 官方参考

- [openpyxl：只读优化模式与显式关闭 Workbook](https://openpyxl.readthedocs.io/en/stable/optimized.html)
- [openpyxl：load_workbook 的 data_only 与 keep_links 参数](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html)
- [Python zipfile：不可信压缩包与解压资源风险](https://docs.python.org/3/library/zipfile.html#decompression-pitfalls)
