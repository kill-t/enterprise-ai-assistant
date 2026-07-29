from fastapi import APIRouter, status

from app.modules.identity.dependencies import AuthServiceDep
from app.modules.identity.schema.auth_schema import UserRegisterRequest, UserResponse


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
