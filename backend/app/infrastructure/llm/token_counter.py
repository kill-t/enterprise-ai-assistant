class TokenCounter:
    """估算文本占用的上下文长度。"""

    def count(self, text: str) -> int:
        """按字符数临时估算 token；后续替换为模型专用 tokenizer。"""
        return len(text)
