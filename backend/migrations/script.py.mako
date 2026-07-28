"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# Alembic 使用以下字段识别迁移版本及其依赖关系。
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """将数据库结构升级到当前版本。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """撤销当前版本引入的数据库结构变更。"""
    ${downgrades if downgrades else "pass"}
