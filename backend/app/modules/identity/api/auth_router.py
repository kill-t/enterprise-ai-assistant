from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.modules.identity.dependencies import (
    AuthServiceDep,
    CurrentUserDep,
    LoginServiceDep,
)
from app.modules.identity.schema.auth_schema import (
    TokenResponse,
    UserRegisterRequest,
    UserResponse,
)


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["身份认证"],
)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    request: UserRegisterRequest,
    service: AuthServiceDep,
) -> UserResponse:
    """接收注册信息，并返回不包含密码哈希的用户公开数据。"""
    return await service.register(request)


@router.post(
    "/login",
    response_model=TokenResponse,
)
async def login_user(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: LoginServiceDep,
) -> TokenResponse:
    """使用邮箱和密码登录，并返回短期 Bearer Access Token。"""
    return await service.login(
        email=form_data.username,
        password=form_data.password,
    )


@router.get(
    "/me",
    response_model=UserResponse,
)
async def get_me(current_user: CurrentUserDep) -> UserResponse:
    """返回 Bearer Token 对应的当前用户公开信息。"""
    return UserResponse.model_validate(current_user)
