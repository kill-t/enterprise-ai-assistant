from app.core.config import settings
from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.providers.mock_provider import MockProvider
from app.infrastructure.llm.providers.openai_provider import OpenAIProvider
from app.infrastructure.llm.providers.deepseek_provider import DeepSeekProvider


class ProviderFactory:

    @staticmethod
    def create() -> BaseChatProvider:

        provider = settings.default_provider.lower()

        if provider == "mock":
            return MockProvider()

        if provider == "deepseek":
            return DeepSeekProvider()

        if provider == "openai":
            return OpenAIProvider()

        raise ValueError(f"Unsupported provider: {provider}")
