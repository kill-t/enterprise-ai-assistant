# 模块 79：Word DOCX 解析与结构化内容块

## 1. 这个模块解决什么业务问题

企业资料不只有 PDF，项目方案、制度和会议纪要经常是 Word。之前上传 `.docx` 会在入口直接返回
415，即使绕过入口，Parser 注册表也不知道怎样处理它。

本模块打通：

```text
Vue 选择 .docx
→ FastAPI 校验扩展名与 MIME
→ MinIO 保存原始文件
→ 持久化 Worker 下载并校验哈希
→ DocxParser 解析正文结构
→ TokenAwareChunker 保留来源位置
→ Mock/真实 Embedding
→ PostgreSQL Chunk 与 pgvector
→ 检索、SSE sources、引用快照
→ Vue 展示“标题路径 · 表格/列表/段落 N”
```

它复用现有入库、检索和引用链，没有为 Word 再写一套 RAG。

## 2. DOCX 到底是什么

`.docx` 不是一整块二进制正文，而是一个 OOXML ZIP 包，里面包含：

```text
[Content_Types].xml
word/document.xml
word/styles.xml
word/_rels/...
word/media/...
```

`python-docx` 负责解释这些 XML 关系。应用层仍要先检查 ZIP 元数据，因为上传文件来自不可信用户，
不能假设扩展名为 `.docx` 就一定安全、合法。

## 3. 为什么不能分别读取 paragraphs 和 tables

假设 Word 原文顺序是：

```text
段落 A → 表格 → 段落 B
```

如果先遍历 `document.paragraphs`，再遍历 `document.tables`，结果会变成：

```text
段落 A → 段落 B → 表格
```

这会改变事实上下文。当前固定使用 `python-docx 1.2` 的 `iter_inner_content()`，它按正文真实顺序
返回顶层 Paragraph 或 Table。表格转成 Markdown，便于模型理解，也能直接复用前端已有的表格渲染。

## 4. Parser 输出为什么是一块一个 ParsedSection

Word 没有像 PDF 那样可靠、稳定的页码。排版、字体、打印机设置不同都可能改变页数，所以系统不能
伪造“第 3 页”。Word 使用：

```json
{
  "kind": "section",
  "heading_path": ["资助政策", "申请条件"],
  "block_kind": "table",
  "block_index": 0
}
```

- `heading_path` 表示当前标题层级。
- `block_kind` 区分 `paragraph/list/table`。
- `block_index` 是同类逻辑内容块的零基序号，前端展示时加 1。相邻且同标题、同类型的短段落会先
  组成一个逻辑块，再由 Chunker 按 Token 预算切分，避免产生大量缺少上下文的微小 Chunk。

标题前可能已经有正文，因此 `heading_path=[]` 在同时提供块类型和序号时合法；如果标题和块信息都
没有，位置没有导航意义，Pydantic 会拒绝。

## 5. 关键代码入口

### `processing/docx_parser.py`

- `parse()`：读取文件、执行安全检查、解析正文并处理稳定错误。
- `_validate_archive()`：限制 ZIP 条目数、单条和总解压大小、压缩率，拒绝加密包和宏载荷。
- `_parse_body()`：保持段落与表格原顺序，维护标题栈和块序号。
- `_read_heading()`：识别 Heading 1～6 以及常见中文标题样式。
- `_is_list()`：通过 OOXML 编号属性和列表样式判断列表。
- `_table_to_markdown()`：把首行作为表头，并转义单元格中的竖线。

### `processing/schemas.py` 与 `chunker.py`

`ParsedSection` 新增可选 `source_locator`：

```text
旧 PDF/TXT/Markdown Parser
→ 不填写
→ Chunker 根据旧页码/标题字段推断

Word Parser
→ 填写精确 SectionSourceLocator
→ Chunker 原样保留
```

这里的重点是“精确位置由最了解格式的 Parser 产生”，Chunker 只负责文本预算，不应该重新猜测并
丢掉表格编号。

### 上传与前端

后端白名单接受 `.docx` 的标准 MIME，也兼容浏览器无法识别时提交的
`application/octet-stream`。这只是上传元数据校验，不是安全边界；真正文件结构仍由 Parser 检查。

Vue 前端同步增加扩展名、`accept` 和中文提示。引用卡片会展示：

```text
资助政策 / 申请条件 · 表格 1
文档开头 · 段落 1
```

## 6. 正常、失败和事务怎样执行

### 正常路径

Parser 和 Embedding 都在数据库事务外执行，避免解析大文件时长期占用数据库连接。全部 Chunk 和向量
准备完成后，Worker 才在一个 PostgreSQL 事务中：

```text
替换旧 Chunk
→ 写入全部新 Chunk/向量/Locator
→ 文档改为 READY
→ 任务改为 SUCCEEDED
→ commit
```

### 失败路径

- 损坏或伪造 DOCX：`DOCX_INVALID`。
- 只有图片、没有可提取正文：`NO_EXTRACTABLE_TEXT`。
- 宏、加密包、条目过多、解压过大或异常压缩率：`OFFICE_ARCHIVE_UNSAFE`。
- Embedding 或对象存储临时故障：继续使用原有 Worker 重试策略。

解析错误属于文件本身问题，重复运行通常不会自动变好，因此 Worker 会保存失败原因，而不是产生半套
索引。当前不执行宏、不把 ZIP 解压到磁盘，也不做图片 OCR。

## 7. 设计取舍与限制

### 为什么选择 python-docx

它提供稳定的 Paragraph、Table、样式和 OOXML 封装，适合可控的 `.docx` 第一版。直接手写 XML
解析可以更精细，但超链接、合并单元格、样式继承和文档关系处理成本明显更高。

### 为什么暂不使用 MarkItDown

MarkItDown 适合把多种 Office 文件统一转成 Markdown，但本项目还要保存格式专属 Locator。独立 Parser
Adapter 更容易解释“这段内容来自哪个 Word 标题和表格”，以后仍可以把 MarkItDown 作为替代实现。

### 当前明确不支持

- 旧二进制 `.doc` 和宏文档 `.docm`。
- 页眉、页脚、文本框、批注和修订痕迹的完整语义。
- 嵌入图片文字识别；这会在 OCR 模块统一解决。
- 完全还原复杂合并单元格、嵌套表格和视觉布局。
- Word 页码引用，因为 DOCX 本身不保存可靠的最终分页结果。

## 8. 测试分别保护什么

- 真实内存 DOCX 验证段落、表格、列表顺序和嵌套标题路径。
- Chunker 不丢失 Word 表格的结构位置。
- 损坏、空正文、宏载荷和 ZIP 条目上限得到稳定错误。
- Parser 注册表按大小写不敏感的 `.docx` 选择适配器。
- 后端接受标准 Word MIME，前端接受并上传 `.docx`。
- 真实 `DocumentIngestionProcessor` 使用 Mock Embedding 产生 1024 维向量，无需 API Key。
- Vue 引用卡片展示 Word 标题、块类型和一基序号。
- 全量旧格式、检索、聊天和前端测试防止回归。

## 9. 面试表达

> 我把 DOCX 当作不可信 OOXML ZIP 处理：先限制条目数、解压大小和压缩率并拒绝宏，再用
> python-docx 的 iter_inner_content 保持段落与表格原顺序。Parser 生成标题路径加内容块位置，
> Chunker 只负责 Token 分块并保留 Locator，后续 Worker、混合检索、SSE 和引用快照全部复用原链路。
> Word 不伪造页码，结构化引用展示为章节和表格/列表/段落编号。

## 10. 小练习（约 15～30 分钟）

给 `test_docx_parser.py` 增加一个表格单元格内容为：

```text
材料|说明
```

验证生成的 Markdown 中它变成：

```text
材料\|说明
```

答案要点：

- Markdown 使用 `|` 分隔列，不转义会错误增加一列。
- 转义发生在 Parser Adapter，因为这是 Word 表格到统一文本格式的转换规则。
- 测试只检查转换结果，不需要启动 PostgreSQL、Redis、MinIO，也不需要模型密钥。

## 11. 官方参考

- [python-docx Document API：iter_inner_content 按文档顺序返回段落与表格](https://python-docx.readthedocs.io/en/latest/api/document.html)
- [Python zipfile：不可信压缩包与解压资源风险](https://docs.python.org/3/library/zipfile.html#decompression-pitfalls)
