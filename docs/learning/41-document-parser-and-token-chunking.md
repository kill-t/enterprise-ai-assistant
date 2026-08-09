# 文档 Parser 契约与 Token 分块

## 1. 这一小步处于哪里

上一模块已经把原文件保存到 MinIO，并创建了 `PENDING` 文档和入库任务。但系统目前还
不知道怎样理解文件内容。

本模块建立两种内部能力：

```text
Parser：文件流 → 带来源信息的文本段
Chunker：文本段 → 满足 Token 预算的 ChunkDraft
```

它们还没有连接 Celery Worker，也没有写入 `document_chunks`。可以把当前结果理解成
“Worker 未来可以调用的纯处理流水线”。

## 2. 为什么 Parser 和 Chunker 要分开

Parser 关心文件格式：

- PDF 的第几页。
- Markdown 的标题层级。
- TXT 使用什么字符编码。
- 文件是否加密、损坏或没有可提取文字。

Chunker 关心模型输入：

- 一个 Chunk 最多多少 Token。
- 尽量不拆开完整段落。
- 相邻 Chunk 保留多少重叠。
- 页码和标题路径不能串到错误来源。

如果把两者写在一个函数里，增加 Word Parser 时就容易同时改动 Token 分块逻辑。当前
设计允许“新增 Parser，不改 Chunker”和“调整 Chunk 大小，不改 PDF 解析”。

## 3. 三个核心数据结构

入口：`app/modules/knowledge/processing/schemas.py`

### ParsedSection

表示来自同一个来源边界的连续原文：

```text
content
page_number
heading_path
```

PDF 一页对应一个 Section；Markdown 一个标题路径对应一个 Section；TXT 第一版只有一个
Section。

### ParsedDocument

保存有序 Section、PDF 总页数和 Parser 版本：

```text
sections
page_count
parser_version
```

Parser 版本会在 Worker 阶段写入文档。以后修改提取规则，可以判断旧文档是否需要重新
入库。

### ChunkDraft

表示尚未生成 Embedding、尚未写数据库的 Chunk：

```text
chunk_index
content
content_hash
heading_path
page_start / page_end
token_count
chunker_version
```

它叫 Draft，是为了明确“当前不是完整的 `DocumentChunkEntity`”。数据库实体还要求
`embedding_model` 和非空 `embedding`。

## 4. Parser 注册表怎样工作

调用者不应该自己写大量判断：

```python
if filename.endswith(".pdf"):
    ...
elif filename.endswith(".txt"):
    ...
```

`DocumentParserRegistry` 统一维护映射：

```text
.pdf      → PdfParser
.txt      → TextParser
.md       → MarkdownParser
.markdown → MarkdownParser
```

扩展名不区分大小写。未知类型抛出 `UnsupportedDocumentTypeError`，而不是随便选择一个
Parser 尝试。

所有 Parser 实现同一个异步接口：

```python
async def parse(stream: BinaryIO) -> ParsedDocument
```

Worker 以后只依赖 `BaseDocumentParser`，不依赖 pypdf。

## 5. TXT 解析

TXT 第一版只支持 UTF-8，也接受 UTF-8 BOM。解析过程：

```text
文件流 seek(0)
→ 在线程池读取临时文件
→ utf-8-sig 解码
→ CRLF / CR 统一为 LF
→ 清理首尾空白
```

失败分类：

- 非 UTF-8：`INVALID_TEXT_ENCODING`。
- 只有空格和换行：`NO_EXTRACTABLE_TEXT`。

为什么不使用系统默认编码？同一文件在 Windows 和 Linux 上可能得到不同结果，无法形成
可复现的 SHA、Chunk 和评测结果。

## 6. Markdown 标题路径

Markdown Parser 使用一个标题栈：

```markdown
# 指南
## 安装
### Windows
```

对应：

```text
指南 > 安装 > Windows
```

遇到新的同级标题时替换当前层级，遇到更低级标题时保留父级。标题行本身也保留在原文中，
这样以后生成 Embedding 时能够检索标题关键词。

代码围栏需要特殊处理：

````markdown
```python
# 这里是代码注释，不是 Markdown 标题
```
````

Parser 在围栏内部不会识别标题，否则代码示例会错误改变后续文档结构。

## 7. PDF 页码与失败处理

PDF 使用 pypdf。它是同步库，因此整个解析函数通过 `asyncio.to_thread` 在线程池执行，
不会占住异步事件循环。

每个页面按从 1 开始的真实页码提取：

```text
第 1 页有文本 → ParsedSection(page_number=1)
第 2 页空白   → 跳过 Section
第 3 页有文本 → ParsedSection(page_number=3)
```

空白页被跳过，但第 3 页仍然是 3，不能因为前面空白而变成 2。回答引用页码必须来自事实，
不能根据有效文本页重新编号。

失败分类：

- 加密 PDF：`PDF_ENCRYPTED`。
- 损坏或格式无效：`PDF_INVALID`。
- 所有页面都无文字：`NO_EXTRACTABLE_TEXT`。

pypdf 不是 OCR。扫描型 PDF 通常只有图片，因此第一版明确失败，不会生成空索引或假装
入库成功。官方文档也说明扫描页可能无法提取有效文字。

## 8. Token Chunker 的规则

默认目标仍是：

```text
Chunk 上限：600 token
相邻重叠：80 token
```

Chunker 接收一个最小 `count(text)` 接口。生产代码可以直接传入现有 `TokenCounter`，已知
模型使用 tokenizer，未知模型使用保守估算。这里没有退回 `len(text)`。

分块顺序：

```text
一个 ParsedSection
→ 按空行识别完整段落
→ 在预算内尽量合并段落
→ 超预算时输出当前 Chunk
→ 从前一 Chunk 结尾取受限重叠
→ 开始下一 Chunk
```

只有单个段落本身超过预算时，才通过字符前缀二分查找强制拆开。因为通用 TokenCounter
只提供计数、不提供 tokenizer 的 decode，所以 Chunker不能假设某个厂商编码器存在。

## 9. 为什么第一版不跨页

Chunker 每次只处理一个 ParsedSection，并在 Section 之间清空重叠。因此：

```text
PDF 第 1 页的结尾
不会复制到第 2 页 Chunk
```

这可能比允许跨页产生更多小 Chunk，但引用关系最清楚：第一版 PDF Chunk 的
`page_start == page_end`。

Markdown 标题路径同理。不同标题的正文不会因为重叠进入另一个标题的 Chunk。

以后可以实验允许相邻页面合并，并同时保存 `page_start/page_end`，但必须通过引用有效率
评测证明收益。

## 10. 重叠为什么要预留预算

假设上限 600、重叠 80。单个超长段落不能这样切：

```text
第一片新正文 600
第二片新正文 600
```

第二片已经占满，没有空间放前一片的 80 token。

当前策略是：

```text
第一片最多 600
后续新正文最多 520
再加前片结尾最多 80
```

最终每个 Chunk 仍不超过 600。若一个普通完整段落接近 600，为了优先保留完整段落，实际
重叠可能缩短；“不超过 80”是硬约束，“一定正好 80”不是。

## 11. 为什么现在不写数据库

当前表要求：

```text
document_chunks.embedding NOT NULL
```

Parser 和 Chunker 只能生成文本与来源，不能伪造向量。如果现在写库，会出现三种坏方案：

- 写全零假向量。
- 临时把 embedding 改成可空。
- 留下一批不能检索的半成品 Chunk。

因此实际依赖顺序调整为：

```text
Parser + Chunker
→ Embedding Provider
→ Worker：下载、解析、分块、向量化、原子写入
→ 检索
```

这没有改变最终架构，只是把实现顺序按代码依赖排得更清楚。

## 12. 测试保护什么

- TXT 的 UTF-8 BOM、换行统一、编码错误和空文本。
- Markdown 嵌套标题、代码围栏和标题路径。
- Parser 注册表的大小写扩展名与未知类型。
- PDF 页码、空白页、加密、扫描件和损坏错误。
- 使用真实 pypdf 生成并提取一页文字，不只依赖 Mock。
- PDF 页与 Markdown 标题不会跨边界合并。
- Chunk 不超过 Token 预算，重叠受限且序号连续。
- 单个超长段落不会产生空 Chunk。
- 真实项目 `TokenCounter` 可以直接接入。
- 相同 Chunk 正文得到稳定 SHA-256。

## 13. 常见错误

| 现象 | 原因与处理 |
| --- | --- |
| 扫描 PDF 返回无文本 | pypdf 不是 OCR；第一版明确失败。 |
| PDF 页码少了一页 | 过滤空白页后错误重新编号；必须保留原始 enumerate 页码。 |
| 代码中的 `#` 变成标题 | Markdown Parser 没有识别 fenced code。 |
| 每个 Chunk 都刚好按字符数切 | 没有复用 TokenCounter，中文和英文预算会失真。 |
| Chunk 超过 600 | 加重叠时没有重新计算整个新 Chunk。 |
| 第二页含有第一页结尾 | 重叠跨越 ParsedSection，引用边界被破坏。 |
| Parser 异常直接保存英文堆栈 | 没有转换为稳定错误码，可能泄露内部实现。 |

## 14. 面试表达

> 我把格式解析和 Token 分块拆成独立纯处理层。PDF 按真实页码产生 Section，Markdown
> 维护标题栈并识别代码围栏，TXT 固定 UTF-8。Chunker 复用模型感知 TokenCounter，优先
> 保持完整段落，只有超长段落才强制切分；重叠不会跨页或跨标题。当前产物是 ChunkDraft，
> 因为数据库向量列非空，必须等 Embedding Provider 完成后由 Worker 原子写入，避免半成品
> 索引。

## 15. 小练习

请用 15–30 分钟增加一个测试：

```markdown
## 安装说明 ##
正文
```

断言 `heading_path` 是 `("安装说明",)`，不能包含末尾的 `##`。

本次只需要增加测试，不修改主体代码。检查重点：

- 测试应调用真实 `MarkdownParser`。
- 同时检查标题路径和 Section 正文仍包含原始标题行。
- 理解“用于检索的结构化标题”和“用于引用的原文”可以不同。

## 16. 参考资料

- [pypdf PdfReader 官方接口](https://pypdf.readthedocs.io/en/latest/modules/PdfReader.html)
- [pypdf 官方文本提取说明](https://pypdf.readthedocs.io/en/latest/user/extract-text.html)
