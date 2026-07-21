class TokenCounter:

    def count(
        self,
        text: str,
    ) -> int:
        # TODO: 后续可替换为 tiktoken 或其他模型专用 tokenizer
        return len(text)
