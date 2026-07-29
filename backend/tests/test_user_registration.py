import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.security.password_hasher import PasswordHasher
from app.main import create_app
from app.modules.identity.entity.user import UserEntity
from app.modules.identity.dependencies import get_auth_service
from app.modules.identity.schema.auth_schema import UserRegisterRequest
from app.modules.identity.schema.auth_schema import UserResponse
from app.modules.identity.service.auth_service import AuthService


def test_password_hasher_never_returns_plaintext_and_can_verify() -> None:
    """密码必须转换为 Argon2 哈希，并能校验正确与错误密码。"""
    hasher = PasswordHasher()

    password_hash = asyncio.run(hasher.hash("correct-password"))

    assert password_hash != "correct-password"
    assert password_hash.startswith("$argon2")
    assert asyncio.run(hasher.verify("correct-password", password_hash)) is True
    assert asyncio.run(hasher.verify("wrong-password", password_hash)) is False


def test_registration_schema_normalizes_email_and_rejects_short_password() -> None:
    """注册结构应统一邮箱大小写，并拒绝低于最小长度的密码。"""
    request = UserRegisterRequest(
        email="Learner@Example.COM",
        password="long-password",
    )

    assert request.email == "learner@example.com"

    with pytest.raises(ValidationError):
        UserRegisterRequest(
            email="learner@example.com",
            password="short",
        )


def test_register_hashes_password_and_commits_user() -> None:
    """注册成功时应只向 Repository 传递密码哈希，并提交一次事务。"""
    now = datetime.now(timezone.utc)
    user = UserEntity(
        id=uuid4(),
        email="learner@example.com",
        password_hash="argon2-hash",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    password_hasher = SimpleNamespace(hash=AsyncMock(return_value="argon2-hash"))
    service = AuthService(db=db, password_hasher=password_hasher)
    service.repository = SimpleNamespace(
        get_by_email=AsyncMock(return_value=None),
        create=AsyncMock(return_value=user),
    )

    response = asyncio.run(
        service.register(
            UserRegisterRequest(
                email="learner@example.com",
                password="plain-password",
            )
        )
    )

    assert response.email == "learner@example.com"
    assert not hasattr(response, "password_hash")
    password_hasher.hash.assert_awaited_once_with("plain-password")
    service.repository.create.assert_awaited_once_with(
        email="learner@example.com",
        password_hash="argon2-hash",
    )
    db.commit.assert_awaited_once_with()
    db.rollback.assert_not_awaited()


def test_register_rejects_existing_email_before_hashing() -> None:
    """邮箱已存在时应立即拒绝注册，避免无意义的高成本密码哈希。"""
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    password_hasher = SimpleNamespace(hash=AsyncMock())
    service = AuthService(db=db, password_hasher=password_hasher)
    service.repository = SimpleNamespace(
        get_by_email=AsyncMock(return_value=object()),
        create=AsyncMock(),
    )

    async def scenario() -> BusinessException:
        """捕获重复邮箱对应的业务异常。"""
        try:
            await service.register(
                UserRegisterRequest(
                    email="learner@example.com",
                    password="plain-password",
                )
            )
        except BusinessException as exc:
            return exc
        raise AssertionError("重复邮箱必须拒绝注册")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.EMAIL_ALREADY_REGISTERED
    assert error.status_code == 409
    password_hasher.hash.assert_not_awaited()
    service.repository.create.assert_not_awaited()


def test_register_rolls_back_concurrent_unique_email_conflict() -> None:
    """并发注册触发数据库唯一约束时应回滚，并转换为稳定业务错误。"""
    now = datetime.now(timezone.utc)
    user = UserEntity(
        id=uuid4(),
        email="learner@example.com",
        password_hash="argon2-hash",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db = SimpleNamespace(
        commit=AsyncMock(
            side_effect=IntegrityError("INSERT users", {}, Exception("unique"))
        ),
        rollback=AsyncMock(),
    )
    password_hasher = SimpleNamespace(hash=AsyncMock(return_value="argon2-hash"))
    service = AuthService(db=db, password_hasher=password_hasher)
    service.repository = SimpleNamespace(
        get_by_email=AsyncMock(return_value=None),
        create=AsyncMock(return_value=user),
    )

    async def scenario() -> BusinessException:
        """捕获数据库并发冲突转换后的业务异常。"""
        try:
            await service.register(
                UserRegisterRequest(
                    email="learner@example.com",
                    password="plain-password",
                )
            )
        except BusinessException as exc:
            return exc
        raise AssertionError("数据库唯一约束冲突必须转换为业务异常")

    error = asyncio.run(scenario())

    assert error.code == ErrorCode.EMAIL_ALREADY_REGISTERED
    assert error.status_code == 409
    db.rollback.assert_awaited_once_with()


def test_register_route_is_exposed_under_versioned_auth_api() -> None:
    """应用应暴露版本化的用户注册 HTTP 接口。"""
    app = create_app()
    register_operation = app.openapi()["paths"]["/api/v1/auth/register"]

    assert "post" in register_operation


def test_register_http_response_never_exposes_password_hash() -> None:
    """注册 HTTP 响应应返回 201，且任何密码字段都不能出现在响应正文。"""
    now = datetime.now(timezone.utc)
    service = SimpleNamespace(
        register=AsyncMock(
            return_value=UserResponse(
                id=uuid4(),
                email="learner@example.com",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    )
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: service

    async def scenario():
        """通过 ASGI 传输直接请求异步应用，不启动真实网络端口。"""
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "Learner@Example.COM",
                    "password": "plain-password",
                },
            )

    response = asyncio.run(scenario())

    assert response.status_code == 201
    assert response.json()["email"] == "learner@example.com"
    assert "password" not in response.text
    service.register.assert_awaited_once()
