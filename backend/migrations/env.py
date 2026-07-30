from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.infrastructure.database.base import Base

# 必须导入全部实体，Alembic 才能从 Base.metadata 发现表结构。
from app.modules.conversation.entity.conversation import ConversationEntity  # noqa: F401, E402
from app.modules.conversation.entity.conversation_summary import ConversationSummaryEntity  # noqa: F401, E402
from app.modules.conversation.entity.message import MessageEntity  # noqa: F401, E402
from app.modules.identity.entity.user import UserEntity  # noqa: F401, E402


# Alembic 配置对象保存 alembic.ini 中的配置；数据库地址以应用配置为准。
config = context.config
config.set_main_option("sqlalchemy.url", settings.database.url)

# 复用 Alembic 配置文件中的日志设置。
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 自动生成迁移时，Alembic 会拿实体元数据与数据库结构进行比较。
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线生成迁移 SQL，不建立真实数据库连接。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """建立同步连接并在事务中执行实际数据库迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
