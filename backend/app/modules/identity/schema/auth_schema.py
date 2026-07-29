from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserRegisterRequest(BaseModel):
    """用户注册时允许客户端提交的邮箱和明文密码。"""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        """统一邮箱大小写，保证应用查询与数据库唯一约束语义一致。"""
        return str(value).lower()


class UserResponse(BaseModel):
    """可安全返回给客户端的用户公开信息，不包含密码哈希。"""

    id: UUID
    email: EmailStr
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """登录成功后返回的 Bearer Token 和有效期。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
