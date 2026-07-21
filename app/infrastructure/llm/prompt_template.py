class PromptTemplate:
    """
    Enterprise AI Assistant Prompt Templates.

    Later this class can provide different prompt
    templates for chat, RAG, SQL, Agent, etc.
    """

    DEFAULT_SYSTEM_PROMPT = """
You are Enterprise AI Assistant.

You are helpful, accurate and concise.

Use Markdown formatting whenever appropriate.
""".strip()

    @classmethod
    def chat(cls) -> str:
        return cls.DEFAULT_SYSTEM_PROMPT
