from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.config import settings
from app.core.security.password_hasher import PasswordHasher
from app.core.security.token_service import TokenService
from app.infrastructure.database.session import get_db
from app.modules.identity.entity.user import UserEntity
from app.modules.identity.repository.user_repository import UserRepository
from app.modules.identity.service.auth_service import AuthService


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


@lru_cache
def get_password_hasher() -> PasswordHasher:
    """复用无状态密码哈希组件，避免每个请求重复构造。"""
    return PasswordHasher()


@lru_cache
def get_token_service() -> TokenService:
    """根据环境配置创建并缓存 JWT TokenService。"""
    secret_key = settings.auth.secret_key
    if secret_key is None:
        raise RuntimeError("未配置 AUTH__SECRET_KEY，无法使用登录功能")
    return TokenService(
        secret_key=secret_key.get_secret_value(),
        algorithm=settings.auth.algorithm,
        access_token_expire_minutes=settings.auth.access_token_expire_minutes,
    )


def get_auth_service(
    db: AsyncSession = Depends(get_db),
    password_hasher: PasswordHasher = Depends(get_password_hasher),
) -> AuthService:
    """创建不依赖 JWT 配置的注册服务。"""
    return AuthService(db=db, password_hasher=password_hasher)


def get_login_service(
    db: AsyncSession = Depends(get_db),
    password_hasher: PasswordHasher = Depends(get_password_hasher),
    token_service: TokenService = Depends(get_token_service),
) -> AuthService:
    """创建同时具备密码校验和 Token 签发能力的登录服务。"""
    return AuthService(
        db=db,
        password_hasher=password_hasher,
        token_service=token_service,
    )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
    token_service: TokenService = Depends(get_token_service),
) -> UserEntity:
    """解析 Bearer Token，并从数据库恢复当前启用用户。"""
    user_id = token_service.decode_user_id(token)
    user = await UserRepository(db).get_by_id(user_id)
    if user is None:
        raise TokenService.invalid_token_exception()
    if not user.is_active:
        raise BusinessException(
            code=ErrorCode.ACCOUNT_DISABLED,
            message="账号已停用",
            status_code=403,
        )
    return user


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
LoginServiceDep = Annotated[AuthService, Depends(get_login_service)]
CurrentUserDep = Annotated[UserEntity, Depends(get_current_user)]
