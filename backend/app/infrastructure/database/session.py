from collections.abc import AsyncGenerator

from pgvector.psycopg import register_vector_async
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def to_async_database_url(database_url: str) -> str:
    """将普通 PostgreSQL URL 转换为 psycopg 异步方言 URL。"""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    return database_url


engine = create_async_engine(
    to_async_database_url(settings.database.url),
    echo=False,
    pool_pre_ping=True,
)


def register_pgvector_types(dbapi_connection, connection_record) -> None:
    """为每条 psycopg 异步连接注册 vector 编解码器。"""
    del connection_record

    # SQLAlchemy 暴露的是同步适配连接；run_async 会在原异步驱动连接上执行注册函数。
    dbapi_connection.run_async(register_vector_async)


event.listen(engine.sync_engine, "connect", register_pgvector_types)

SessionLocal = async_sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """为单次请求提供异步 Session，并在异常时回滚未提交事务。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
