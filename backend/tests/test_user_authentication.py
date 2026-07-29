import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.security.token_service import AccessToken, TokenService
from app.main import create_app
from app.modules.identity.dependencies import get_current_user, get_login_service
from app.modules.identity.entity.user import UserEntity
from app.modules.identity.service.auth_service import AuthService
from app.modules.identity.schema.auth_schema import TokenResponse


def test_access_token_round_trip_preserves_user_id() -> None:
    """签发后的 Access Token 应能验证签名并恢复用户 UUID。"""
    user_id = uuid4()
    token_service = TokenService(
        secret_key="test-secret-key-at-least-32-bytes-long",
        algorithm="HS256",
        access_token_expire_minutes=30,
    )

    access_token = token_service.create_access_token(user_id)

    assert access_token.token_type == "bearer"
    assert access_token.expires_in == 1800
    assert token_service.decode_user_id(access_token.token) == user_id


@pytest.mark.parametrize("token_kind", ["expired", "tampered"])
def test_invalid_access_token_is_rejected(token_kind: str) -> None:
    """过期或被篡改的 Token 都必须转换为统一未认证错误。"""
    token_service = TokenService(
        secret_key="test-secret-key-at-least-32-bytes-long",
        algorithm="HS256",
        access_token_expire_minutes=30,
    )
    if token_kind == "expired":
        token = token_service.create_access_token(
            uuid4(),
            expires_delta=timedelta(seconds=-1),
        ).token
    else:
        token = f"{token_service.create_access_token(uuid4()).token}tampered"

    with pytest.raises(BusinessException) as captured:
        token_service.decode_user_id(token)

    assert captured.value.code == ErrorCode.INVALID_TOKEN
    assert captured.value.status_code == 401


def test_login_verifies_password_and_returns_access_token() -> None:
    """有效邮箱和密码应签发 Access Token，且不修改数据库事务。"""
    user = build_user()
    db = SimpleNamespace()
    password_hasher = SimpleNamespace(verify=AsyncMock(return_value=True))
    token_service = SimpleNamespace(
        create_access_token=lambda user_id: AccessToken(
            token=f"token-for-{user_id}",
            token_type="bearer",
            expires_in=1800,
        )
    )
    service = AuthService(
        db=db,
        password_hasher=password_hasher,
        token_service=token_service,
    )
    service.repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))

    response = asyncio.run(
        service.login(
            email="Learner@Example.COM",
            password="plain-password",
        )
    )

    assert response.access_token == f"token-for-{user.id}"
    assert response.token_type == "bearer"
    service.repository.get_by_email.assert_awaited_once_with("learner@example.com")
    password_hasher.verify.assert_awaited_once_with(
        "plain-password",
        user.password_hash,
    )


def test_login_uses_same_error_for_missing_user_and_wrong_password() -> None:
    """不存在用户和错误密码应返回相同提示，减少账号枚举信息。"""
    password_hasher = SimpleNamespace(verify=AsyncMock(return_value=False))
    token_service = SimpleNamespace(create_access_token=lambda user_id: None)
    service = AuthService(
        db=SimpleNamespace(),
        password_hasher=password_hasher,
        token_service=token_service,
    )
    service.repository = SimpleNamespace(get_by_email=AsyncMock(return_value=None))

    missing_user_error = asyncio.run(
        capture_login_error(service, "missing@example.com", "plain-password")
    )

    service.repository.get_by_email = AsyncMock(return_value=build_user())
    wrong_password_error = asyncio.run(
        capture_login_error(service, "learner@example.com", "wrong-password")
    )

    assert missing_user_error.code == ErrorCode.INVALID_CREDENTIALS
    assert wrong_password_error.code == ErrorCode.INVALID_CREDENTIALS
    assert missing_user_error.message == wrong_password_error.message
    assert missing_user_error.status_code == 401


def test_inactive_user_cannot_log_in() -> None:
    """密码正确但账号停用时应拒绝签发 Token。"""
    user = build_user(is_active=False)
    password_hasher = SimpleNamespace(verify=AsyncMock(return_value=True))
    token_service = SimpleNamespace(create_access_token=lambda user_id: None)
    service = AuthService(
        db=SimpleNamespace(),
        password_hasher=password_hasher,
        token_service=token_service,
    )
    service.repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))

    error = asyncio.run(
        capture_login_error(service, user.email, "plain-password")
    )

    assert error.code == ErrorCode.ACCOUNT_DISABLED
    assert error.status_code == 403


def test_current_user_dependency_restores_active_user() -> None:
    """Bearer Token 应解析用户 ID，并从数据库恢复当前启用用户。"""
    user = build_user()
    db = SimpleNamespace(scalar=AsyncMock(return_value=user))
    token_service = SimpleNamespace(decode_user_id=lambda token: user.id)

    current_user = asyncio.run(
        get_current_user(
            token="valid-token",
            db=db,
            token_service=token_service,
        )
    )

    assert current_user is user
    db.scalar.assert_awaited_once()


def test_login_and_current_user_routes_are_exposed() -> None:
    """OpenAPI 应暴露 OAuth2 登录和当前用户接口。"""
    paths = create_app().openapi()["paths"]

    assert "post" in paths["/api/v1/auth/login"]
    assert "get" in paths["/api/v1/auth/me"]


def test_login_http_endpoint_accepts_oauth2_form() -> None:
    """登录接口应按 OAuth2 表单接收邮箱和密码，并返回 Bearer Token。"""
    service = SimpleNamespace(
        login=AsyncMock(
            return_value=TokenResponse(
                access_token="signed-access-token",
                token_type="bearer",
                expires_in=1800,
            )
        )
    )
    app = create_app()
    app.dependency_overrides[get_login_service] = lambda: service

    async def scenario():
        """通过 ASGI 传输提交标准 OAuth2 表单。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/v1/auth/login",
                data={
                    "username": "Learner@Example.COM",
                    "password": "plain-password",
                },
            )

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "signed-access-token",
        "token_type": "bearer",
        "expires_in": 1800,
    }
    service.login.assert_awaited_once_with(
        email="Learner@Example.COM",
        password="plain-password",
    )


def test_current_user_http_response_hides_password_hash() -> None:
    """当前用户接口应返回公开字段，并过滤实体中的密码哈希。"""
    user = build_user()
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user

    async def scenario():
        """通过依赖覆盖请求当前用户接口。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer test-token"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 200
    assert response.json()["id"] == str(user.id)
    assert response.json()["email"] == user.email
    assert "password" not in response.text


def test_login_error_returns_stable_code_and_bearer_header() -> None:
    """登录失败响应应保留稳定错误码和 OAuth2 Bearer 认证头。"""
    service = SimpleNamespace(
        login=AsyncMock(
            side_effect=BusinessException(
                code=ErrorCode.INVALID_CREDENTIALS,
                message="邮箱或密码错误",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        )
    )
    app = create_app()
    app.dependency_overrides[get_login_service] = lambda: service

    async def scenario():
        """提交错误登录表单并返回统一异常响应。"""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.post(
                "/api/v1/auth/login",
                data={
                    "username": "learner@example.com",
                    "password": "wrong-password",
                },
            )

    response = asyncio.run(scenario())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == ErrorCode.INVALID_CREDENTIALS.value


async def capture_login_error(
    service: AuthService,
    email: str,
    password: str,
) -> BusinessException:
    """调用登录并返回预期业务异常，供同步测试断言。"""
    try:
        await service.login(email=email, password=password)
    except BusinessException as exc:
        return exc
    raise AssertionError("登录失败场景必须抛出业务异常")


def build_user(is_active: bool = True) -> UserEntity:
    """创建包含完整响应字段的用户测试实体。"""
    now = datetime.now(timezone.utc)
    return UserEntity(
        id=uuid4(),
        email="learner@example.com",
        password_hash="argon2-hash",
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )
