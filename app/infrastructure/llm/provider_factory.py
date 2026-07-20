from app.infrastructure.llm.base import BaseChatProvider
from app.infrastructure.llm.mock_provider import MockProvider


class ProviderFactory:


    @staticmethod
    def create() -> BaseChatProvider:

        return MockProvider()