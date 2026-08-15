"""封装 llm_runs 的最小写入操作。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm_audit.entity.llm_run import LLMRunEntity


class LLMRunRepository:
    """只访问审计表，不自行决定事务提交。"""

    def __init__(self, db: AsyncSession) -> None:
        """保存本次审计事务使用的异步 Session。"""
        self.db = db

    async def create(self, entity: LLMRunEntity) -> LLMRunEntity:
        """暂存并刷新一条终态调用记录。"""
        self.db.add(entity)
        await self.db.flush()
        return entity
