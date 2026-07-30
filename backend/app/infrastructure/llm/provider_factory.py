from app.core.config import ModelContextSettings, settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.providers.mock_provider import MockProvider
from app.infrastructure.llm.providers.openai_provider import OpenAIProvider
from app.infrastructure.llm.providers.deepseek_provider import DeepSeekProvider


class ProviderFactory:
    """根据配置创建对应的大模型 Provider。"""

    @staticmethod
    def create() -> BaseChatProvider:
        """返回默认 Provider，不支持的名称会立即报错。"""

        provider = settings.default_provider.lower()

        if provider == "mock":
            return MockProvider()

        if provider == "deepseek":
            return DeepSeekProvider()

        if provider == "openai":
            return OpenAIProvider()

        raise ValueError(f"Unsupported provider: {provider}")

    @staticmethod
    def get_context_settings() -> ModelContextSettings:
        """返回默认 Provider 的上下文预算；Mock 使用稳定的本地默认值。"""
        provider = settings.default_provider.lower()
        if provider == "deepseek":
            return settings.deepseek
        if provider == "openai":
            return settings.openai
        if provider == "mock":
            return ModelContextSettings()
        raise ValueError(f"Unsupported provider: {provider}")
