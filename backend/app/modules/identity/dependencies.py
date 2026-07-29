from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import get_db
from app.modules.identity.service.auth_service import AuthService


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    """为当前请求创建共享同一数据库 Session 的认证服务。"""
    return AuthService(db)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
