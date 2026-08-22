# 模块 81：扫描 PDF 与图片 OCR

## 1. 这个模块解决什么业务问题

普通 PDF 可能带有可复制的文本层，也可能只是扫描图片。`pypdf` 只能读取文本层，所以过去上传扫描件会
得到 `NO_EXTRACTABLE_TEXT`。奖学金通知、盖章制度、票据、白板照片等企业资料因此无法进入知识库。

本模块新增：

- PNG、JPG、JPEG 独立图片入库。
- PDF 空文本页面的 OCR 回退。
- OCR 置信度过滤。
- 页码、归一化图片区域、置信度和引擎版本引用。
- 图片尺寸、像素和 PDF 扫描页数量限制。
- 本地 RapidOCR + ONNX Runtime，不需要 API Key。

完整调用链是：

```text
Vue 上传扫描 PDF / PNG / JPEG
→ DocumentService 校验扩展名、MIME 和 20 MB 上传上限
→ MinIO 保存原文件
→ Celery 持久化 Worker 下载并校验哈希
→ DocumentParserRegistry 选择 Parser
   ├─ 图片 → ImageOcrParser
   └─ PDF  → pypdf 读取文本层
             └─ 仅空文本页 → PyMuPDF 渲染 → ImageOcrParser
→ RapidOcrProvider 在线程池运行 ONNX 推理
→ 过滤低置信度行并按阅读顺序预分块
→ TokenAwareChunker → Embedding → pgvector
→ 检索、SSE、引用快照
→ Vue 显示页码/图片与 OCR 置信度
```

OCR 只负责把图像中的字变成可检索文本。后面的分块、Embedding、混合检索、证据选择、拒答和引用
保存全部复用既有 RAG 链路。

## 2. 为什么选 RapidOCR，而不是直接使用 PaddleOCR 或云 OCR

当前作品需要陌生开发者在 Windows 通过一套本地环境复现，默认演示不能依赖第三方账号。因此第一版
选择：

```text
RapidOCR
→ ONNX Runtime CPU
→ 本地 PP-OCRv6 小模型
```

主要取舍：

| 方案 | 优点 | 当前没有选择的原因 |
|---|---|---|
| RapidOCR + ONNX Runtime | 本地、无密钥、Windows 易复现，接口较轻 | 对复杂版面、极低清晰度资料仍有限 |
| PaddleOCR | 完整能力和版面生态更强 | Paddle 运行环境更重，第一版部署复杂度较高 |
| 云 OCR | 精度、票据和表格专用能力通常较好 | 需要密钥、产生费用，文档会离开本地安全边界 |
| 多模态大模型 | 能同时理解图片语义和版面 | 成本与延迟高，不适合把每个文档页面都作为基础入库步骤 |

Provider 已通过 `BaseOcrProvider` 抽象。以后增加 Paddle 或云 Adapter 时，Parser、Worker 和 RAG 不需要
改写。生产环境也可以根据资料敏感等级决定是否允许云 OCR。

## 3. 为什么普通 PDF 不全部做 OCR

PDF 的文本层通常比 OCR 更准确，还保留可复制字符。若每页都 OCR，会产生：

- 重复计算和更高延迟。
- 数字、标点或专有名词识别误差。
- 同一页文本层和 OCR 文本重复入库。
- 更多 CPU 与内存消耗。

所以 `PdfParser` 先用 `pypdf` 获取与页序对齐的文本数组：

```text
第 1 页有文本 → 直接保存 PageSourceLocator
第 2 页无文本 → 调用 PdfOcrPageParser
第 3 页有文本 → 直接保存 PageSourceLocator
```

混合 PDF 的输出仍按原始页序排列。空白装饰页 OCR 不出可靠文字时可以跳过；如果整份扫描 PDF 都没有
可靠结果，则文档失败，不生成空索引。

## 4. 为什么用 PyMuPDF 渲染扫描页

`pypdf` 负责 PDF 结构和文本提取，不负责把页面绘制成图片。`PdfOcrPageParser` 使用 PyMuPDF 的
`Page.get_pixmap(dpi=..., alpha=False)`：

- 默认 200 DPI，在清晰度、速度和内存之间折中。
- `alpha=False` 使用 RGB 白底，减少像素内存并适合 OCR。
- 创建 Pixmap 前按页面尺寸估算像素数。
- 默认每个 PDF 最多 50 个空文本 OCR 页。
- 单页渲染像素默认不超过 2500 万。

官方文档常用 300 DPI 展示高分辨率渲染；项目默认 200 DPI 是业务配置，不是库的限制。低清晰度资料
可以在评测后提高 DPI，但提高 DPI 会按面积增长像素和推理成本。

## 5. 图片安全边界

20 MB 文件上限只限制压缩后的上传大小。一张很小的压缩图片解码后可能拥有极大宽高，消耗大量内存。
因此 `ImageOcrParser` 在完整解码前检查：

- 只允许实际格式为 PNG 或 JPEG，不能只相信扩展名和浏览器 MIME。
- 宽、高必须为正且分别不超过 10000 像素。
- 总像素默认不超过 2500 万。
- Pillow 的 Decompression Bomb 警告按错误处理。
- EXIF 方向先纠正，再次校验纠正后的尺寸。
- 透明 PNG 合成白底 RGB，避免透明区域转黑干扰文字识别。

这体现两个不同安全层：

```text
DocumentService → 文件名、MIME、压缩后字节数
ImageOcrParser  → 实际格式、解码尺寸、总像素
```

客户端前端校验只改善体验，不能替代后端安全检查。

## 6. OCR Provider 的内部契约

第三方 RapidOCR 返回像素四边形、文字和分数。项目内部统一为：

```python
OcrTextLine(
    text="国家助学金申请条件",
    confidence=0.93,
    bounding_box=NormalizedBoundingBox(
        x_min=0.10,
        y_min=0.20,
        x_max=0.85,
        y_max=0.28,
    ),
)
```

坐标除以图片宽高，保存为 0～1。这样引用不依赖 1200×800、2400×1600 等具体像素尺寸，前端以后
可以在任意缩放比例上画出高亮框。

`RapidOcrProvider` 的关键逻辑：

- 模型在第一次识别时懒加载，API/Worker import 不立即初始化权重。
- 同一 Provider 实例只初始化一次模型。
- 同步 ONNX 推理放入 `asyncio.to_thread()`，不阻塞异步数据库事件循环。
- 第三方异常转为 `OcrProviderError`，Parser 再转为稳定业务错误。
- 非法分数、空文字或零面积坐标不会进入内部结果。

## 7. 置信度和 OCR 预分块

默认只保留置信度不低于 `0.65` 的识别行。这个值是初始配置，不应当被描述为任何资料都适用的绝对
标准，后续需要用真实扫描资料评测“漏掉文字”和“保存错字”的取舍。

保留下来的行按：

```text
y_min 从小到大 → x_min 从小到大
```

近似恢复从上到下、从左到右的阅读顺序。然后按最多 20 行或约 500 个字符组成一个来源段。每个来源
段保存组内所有行的最小外接矩形，并使用组内最低置信度作为保守分数。

为什么不先把整张图片合成一段？如果通用 Token Chunker 后来切成多个 Chunk，每个 Chunk 都只能引用
整张图。OCR 先按区域预分块，可以让回答更接近原图具体区域。通用 Chunker 仍负责最终模型 Token
上限。

当前阅读顺序是基础规则。双栏论文、复杂表格、印章覆盖和倾斜版面以后需要版面分析，而不是不断给
排序函数加入特例。

## 8. 结构化引用和旧字段

扫描 PDF 第 4 页的一块 OCR 来源会保存：

```json
{
  "kind": "ocr_region",
  "page_number": 4,
  "bounding_box": {
    "x_min": 0.1,
    "y_min": 0.2,
    "x_max": 0.85,
    "y_max": 0.5
  },
  "confidence": 0.91,
  "engine": "rapidocr-3.9.2"
}
```

独立图片的 `page_number` 为 `null`。`source_type` 是 `image`；扫描 PDF 的 `source_type` 仍是 `pdf`，
因为来源文件事实没有变化。

Chunk、向量检索、关键词检索、RRF、LLM 证据选择、SSE 和 `message_citations` 都传递同一 Locator。
历史回答保存引用快照，文档以后重入库也不会改变旧答案当时看到的区域和置信度。

## 9. 关键代码入口

### `processing/ocr_provider.py`

- `BaseOcrProvider`：本地或云 OCR 的统一异步接口。
- `OcrImage/OcrTextLine/OcrResult`：基础设施和业务 Parser 之间的数据契约。
- `RapidOcrProvider.recognize()`：线程池推理和异常隔离。
- `_normalize_box()`：像素四边形到归一化矩形。

### `processing/image_ocr_parser.py`

- `parse()`：独立 PNG/JPEG 文档入口。
- `parse_image_content()`：独立图片和 PDF 页面共用入口。
- `_decode_and_normalize()`：实际格式、尺寸、EXIF 和透明背景处理。
- `_reliable_lines()`：文字清理、置信度过滤和阅读顺序。
- `_build_sections()`：完整 OCR 行预分块与区域 Locator。

### `processing/pdf_ocr_page_parser.py`

- `_render_page_sync()`：PyMuPDF 渲染前像素估算和真实 Pixmap 生成。
- `parse_page()`：渲染后复用 `ImageOcrParser`，并写入真实一基页码。

### `processing/pdf_parser.py`

- `_extract_page_texts()`：pypdf 文本层和加密检查。
- `parse()`：按原页序选择文本层或 OCR 回退，限制 OCR 页数。

### `processing/parser_registry.py`

同一个 `ImageOcrParser` 和 `RapidOcrProvider` 同时服务 `.png/.jpg/.jpeg` 与扫描 PDF，避免每个格式重复
加载 OCR 模型。

## 10. 正常、失败和事务边界

OCR、PDF 渲染、分块和 Embedding 都在数据库事务外运行。只有全部结果准备完成，Worker 才在一个
PostgreSQL 事务中替换 Chunk、写向量并更新文档和任务终态。

主要错误：

| 错误码 | 含义 | 是否自动重试 |
|---|---|---|
| `IMAGE_INVALID` | 损坏、伪造或非 PNG/JPEG 图片 | 否 |
| `OCR_NO_RELIABLE_TEXT` | 没有达到阈值的文字 | 否 |
| `OCR_LIMIT_EXCEEDED` | 图片像素、尺寸或 PDF OCR 页数超限 | 否 |
| `OCR_ENGINE_FAILED` | 模型运行时临时失败 | 是，走持久化任务退避 |
| `PDF_INVALID` | PDF 损坏或页面无法渲染 | 否 |

OCR 失败不会写入半套 Chunk。`OCR_ENGINE_FAILED` 被单独归为可重试错误；格式错误或低置信度重复运行
通常不会自行变好，因此不浪费重试次数。

## 11. 配置和运行

`.env.example` 提供全部默认值：

```text
OCR__PROVIDER=rapidocr
OCR__MIN_CONFIDENCE=0.65
OCR__PDF_DPI=200
OCR__MAX_PDF_PAGES=50
OCR__MAX_IMAGE_PIXELS=25000000
OCR__MAX_IMAGE_WIDTH=10000
OCR__MAX_IMAGE_HEIGHT=10000
OCR__MAX_LINES_PER_SECTION=20
OCR__MAX_CHARACTERS_PER_SECTION=500
```

OCR 不需要 API Key。`uv sync` 安装 RapidOCR、ONNX Runtime、PyMuPDF 和 Pillow。上传后必须启动文档
入库 Celery Worker；第一次 OCR 会初始化模型，通常比后续调用慢。

## 12. 测试保护什么

- OCR 行按阅读顺序排列，低置信度行被过滤。
- 多行结果按完整行预分块，区域是各行最小外接矩形。
- 无可靠文字、像素过大和无效图片产生稳定错误。
- 混合 PDF 只对无文本页面执行 OCR，原始页序不变。
- 真实 PyMuPDF 能把图片型 PDF 渲染后交给图片 Parser。
- RapidOCR 的四点像素坐标转成稳定归一化内部契约。
- OCR 引擎临时失败进入持久化重试。
- 后端上传白名单和 Vue 文件选择支持 PNG/JPEG。
- 本机真实 RapidOCR 3.9.2 + ONNX Runtime 已加载模型完成推理烟测。
- 旧 PDF、TXT、Markdown、DOCX、XLSX、检索、聊天和引用测试继续通过。

## 13. 当前明确限制

- 不做手写文字专项识别。
- 不做复杂双栏、表格、公式、图章和版面结构还原。
- 不自动纠正低清晰度、强透视或严重倾斜图片。
- 不识别 DOCX/XLSX 内嵌图片；当前 OCR 入口是独立图片和扫描 PDF 页面。
- 不支持 TIFF、BMP、HEIC 等图片格式。
- 扫描 PDF 页按顺序逐页 OCR，第一版不并行争抢 CPU。
- 独立图片引用暂未在前端绘制区域框，只展示“图片”和 OCR 置信度。

这些限制需要通过下一步多格式评测集确定优先级。不能因为 OCR 引擎返回了文字，就把文字准确率、检索
命中率和最终回答正确率混为一个指标。

## 14. 面试表达

> 我把 OCR 设计为 RAG 文档解析适配器，而不是另建一套问答流程。PDF 先用 pypdf 读取文本层，只对
> 空文本页用 PyMuPDF 受限渲染，再交给 RapidOCR 的 ONNX CPU Provider。图片在完整解码前检查实际
> 格式、宽高和总像素，OCR 行按置信度过滤并按区域预分块。引用保存归一化坐标、页码、最低置信度和
> 引擎版本。格式错误不可重试，模型临时故障进入 PostgreSQL 持久化任务重试，最终 Chunk 和向量仍
> 通过原有原子事务写入。

## 15. 小练习（约 15～30 分钟）

在 `test_ocr_processing.py` 增加一条与第一行 `y_min` 相同、但 `x_min` 更小的 OCR 行，验证它排在第一
行前面。

答案要点：

- 当前阅读顺序键是 `(y_min, x_min)`。
- 只修改 Stub Provider 输入，不需要真实 OCR 模型、数据库或 MinIO。
- 测试应验证最终 `section.content` 行顺序，而不是测试 Python `sorted()` 本身。
- 这个练习也会暴露基础排序对双栏版面并不可靠，复杂版面应进入独立 Layout Adapter。

## 16. 官方参考

- [RapidOCR：Python 使用与默认 PP-OCRv6 小模型](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)
- [RapidOCR：ONNX Runtime 是默认推理引擎](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/how_to_use_infer_engine/)
- [ONNX Runtime：Python CPU 安装](https://onnxruntime.ai/docs/install/)
- [PyMuPDF：页面渲染与 DPI](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)
- [Pillow：图片打开与解压炸弹保护](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.open)
