class PromptTemplate:
    """集中管理聊天、RAG 和 Agent 等场景的提示词模板。"""

    DEFAULT_SYSTEM_PROMPT = """
You are Enterprise AI Assistant.

You are helpful, accurate and concise.

Use Markdown formatting whenever appropriate.
""".strip()

    @classmethod
    def chat(cls) -> str:
        """返回普通聊天场景的默认系统提示词。"""
        return cls.DEFAULT_SYSTEM_PROMPT
