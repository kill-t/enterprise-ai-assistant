# Token 预算与完整轮次裁剪

## 1. 本模块解决什么问题

模型的上下文窗口不是无限的。历史消息持续增加后，如果把所有消息直接发送给模型，模型接口会拒绝请求，或者挤占本应留给回答的空间。

旧实现存在两个问题：

- 用 `len(text)` 统计字符数，不是真正的 token 估算。
- 按单条消息裁剪，可能只留下助手回答，却删掉对应的用户问题。

本模块把上下文构建改为“按模型估算 token，并按完整问答轮次裁剪”。滚动摘要将在下一个独立模块实现。

## 2. 预算公式

```text
可用输入 token
= 模型上下文窗口
- 最大输出 token
- 安全余量
```

例如：

```text
模型上下文窗口：8192
最大输出预留：1024
安全余量：256
可用输入预算：6912
```

在 6912 中，系统提示词和当前用户问题必须先占用预算，剩余部分才能放历史消息。

```text
历史消息预算
= 可用输入预算
- System Prompt token
- 当前用户问题 token
```

如果 System Prompt 和当前问题已经超过可用输入预算，系统不会偷偷截断用户正文，而是返回 `CONTEXT_WINDOW_EXCEEDED`，提示用户缩短内容。

## 3. 什么是完整轮次

普通聊天的一轮是：

```text
user：用户问题
assistant：助手回答
```

历史可能是：

```text
第一轮：问题 1 + 回答 1
第二轮：问题 2 + 回答 2
第三轮：问题 3 + 回答 3
当前问题：问题 4
```

预算不足时，从最新历史向前选择：

```text
System Prompt
第二轮
第三轮
当前问题 4
```

第一轮被整体删除，不能只留下回答 1。只有 user 开始、assistant 结束的历史才算完整轮次；孤立助手消息和尚未回答的历史问题不会进入模型上下文。

## 4. 真实调用链

```text
ConversationService.send_message
→ 从 Redis 或 PostgreSQL 读取已完成历史
→ PromptBuilder 解析最终 System Prompt
→ ContextManager 计算可用输入预算
→ TokenCounter 为当前模型估算 token
→ ContextManager 按完整轮次选择最近历史
→ PromptBuilder 构造 ChatRequest
→ Provider 调用模型
```

关键入口：

- `app/infrastructure/llm/token_counter.py`：只负责 token 估算。
- `app/infrastructure/llm/context_manager.py`：只负责预算和轮次选择。
- `app/infrastructure/llm/prompt_builder.py`：组装最终模型请求。
- `app/modules/conversation/service/conversation_service.py`：编排完整业务流程。
- `app/core/config.py`：保存不同 Provider 的上下文预算参数。

## 5. 为什么需要 TokenCounter 接口

不同模型使用的 tokenizer 可能不同，因此不能把某一种编码规则写死在 Service 中。

当前策略是：

1. 已知模型优先通过 `tiktoken` 加载对应编码器。
2. 模型未知、依赖缺失或编码表加载失败时，按 UTF-8 字节保守估算。
3. 降级估算时记录中文警告日志，但聊天继续执行。

保守估算采用：

```text
ceil(UTF-8 字节数 / 3)
```

中文字符通常占三个 UTF-8 字节；英文模型中常见情况约为四个字符一个 token，因此按三个字节估算会留出更多空间。

这不是精确账单数据。精确 token 用量仍以模型返回的 usage 为准；请求前估算只是为了避免上下文超限。

## 6. 本次遇到的典型错误

安装 `tiktoken` 后，真实测试出现了网络连接失败。堆栈显示：

```text
TokenCounter
→ tiktoken.encoding_for_model
→ 首次下载模型编码表
→ 网络访问被拒绝
```

问题不在 ConversationService，也不在模型 API，而在 tokenizer 的初始化边界。

修复方式是在 `_load_model_encoder` 捕获加载异常，记录降级日志并使用保守估算。同时对加载结果进行缓存，避免同一模型反复初始化。

阅读堆栈时应从最下方异常开始，再沿调用链向上找到“我们自己的第一层代码”。这次对应的就是 `TokenCounter._load_model_encoder`。

## 7. 测试分别保护什么

- 注入假编码器：验证模型 tokenizer 路径。
- 未知模型：验证 UTF-8 保守估算。
- tokenizer 加载失败：验证离线时聊天仍可继续。
- 预算计算：验证已扣除输出预留和安全余量。
- 长历史：验证只保留最新完整轮次。
- 超大最新轮次：验证不会保留半轮消息。
- 必选内容超限：验证返回稳定业务错误码。

这些属于单元测试和 Service 边界测试，不调用真实模型，也不依赖 Redis 或 PostgreSQL。

## 8. 设计取舍

### 当前方案：模型 tokenizer + 保守降级

优点：常见模型估算较准确，离线或未知模型仍可使用。

缺点：估算不是模型厂商最终计费值，部分编码表第一次使用可能需要下载。

### 备选方案：全部按字符数

实现最简单，但中英文差异大，也无法体现模型差异，因此不采用。

### 备选方案：只使用模型精确 tokenizer

精度更高，但 tokenizer 不支持新模型或编码表不可用时会让聊天整体失败，因此第一版不采用强依赖策略。

## 9. 面试表达

可以这样说明：

> 我把 PostgreSQL 中的完整历史与模型实际输入分开管理。原始消息永远保留，模型输入则先扣除输出预留和安全余量，再使用模型 tokenizer 估算 System Prompt、当前问题和历史轮次。历史从新到旧按完整 user/assistant 轮次选择，避免出现半轮上下文。tokenizer 不可用时会降级到保守 UTF-8 估算，而不是让聊天失败。

## 10. 小练习

假设：

```text
可用输入预算：100 token
System Prompt：20 token
当前问题：20 token
第一轮历史：25 token
第二轮历史：30 token
第三轮历史：20 token
```

请回答：

1. 历史消息还有多少 token 预算？
2. 从最新向前选择，最终会保留哪些完整轮次？
3. 为什么不能保留“第一轮的一半”？

答案要点：历史预算为 60；先保留第三轮和第二轮，共 50；第一轮加入后会达到 75，超过预算，因此整体丢弃第一轮。
