"""保留会话字段迁移版本。

Revision ID: 279b38fa70fc
Revises: c6b9cb35ce16
Create Date: 2026-07-21 16:39:28.857940

"""
from typing import Sequence, Union

# Alembic 迁移版本及其依赖关系。
revision: str = '279b38fa70fc'
down_revision: Union[str, Sequence[str], None] = 'c6b9cb35ce16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """字段已由前序迁移创建，因此该历史版本无需执行操作。"""
    pass


def downgrade() -> None:
    """当前版本未修改结构，因此回退时也无需执行操作。"""
    pass
