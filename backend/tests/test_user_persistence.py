import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from app.modules.identity.entity.user import UserEntity
from app.modules.identity.repository.user_repository import UserRepository


def test_user_entity_stores_password_hash_and_unique_email() -> None:
    """用户实体只能保存密码哈希，并通过数据库约束保证邮箱唯一。"""
    table = UserEntity.__table__

    assert "password" not in table.columns
    assert table.c.password_hash.nullable is False
    assert table.c.email.type.length == 320
    assert table.c.email.nullable is False
    assert any(
        constraint.name == "uq_users_email"
        for constraint in table.constraints
    )


def test_user_repository_creates_user_without_committing_transaction() -> None:
    """Repository 应刷新新用户，但事务提交必须留给上层 Service。"""
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    repository = UserRepository(db)

    user = asyncio.run(
        repository.create(
            email="learner@example.com",
            password_hash="hashed-password",
        )
    )

    assert user.email == "learner@example.com"
    assert user.password_hash == "hashed-password"
    db.add.assert_called_once_with(user)
    db.flush.assert_awaited_once_with()
    db.refresh.assert_awaited_once_with(user)
    assert not hasattr(db, "commit")


def test_user_repository_queries_by_email_and_id() -> None:
    """Repository 应提供登录和当前用户查询所需的邮箱、主键入口。"""
    user = UserEntity(
        id=uuid4(),
        email="learner@example.com",
        password_hash="hashed-password",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=user))
    repository = UserRepository(db)

    by_email = asyncio.run(repository.get_by_email(user.email))
    by_id = asyncio.run(repository.get_by_id(user.id))

    assert by_email is user
    assert by_id is user
    assert db.scalar.await_count == 2
