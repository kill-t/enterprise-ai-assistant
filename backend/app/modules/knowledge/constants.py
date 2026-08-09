"""知识库模块第一版使用的稳定默认配置。"""

# 创建知识库时保存配置快照，后续切换真实 Provider 不会悄悄混用旧向量。
DEFAULT_EMBEDDING_PROVIDER = "mock"
DEFAULT_EMBEDDING_MODEL = "mock-hash-1024"
DEFAULT_EMBEDDING_DIMENSION = 1024
