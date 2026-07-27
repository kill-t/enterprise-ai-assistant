from abc import ABC, abstractmethod
from app.infrastructure.llm.schemas import (
    ChatRequest,
    ChatResponse,
)


class BaseChatProvider(ABC):

    @abstractmethod
    def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        """
        Send messages to LLM and return response.

        messages example:

        [
            {
                "role": "user",
                "content": "hello"
            }
        ]

        """

        raise NotImplementedError


"""TODO:@abstractmethod
def provider_name(self) -> str:


@property
@abstractmethod
def model_name(self) -> str:"""
