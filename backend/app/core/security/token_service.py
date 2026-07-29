from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode


@dataclass(frozen=True)
class AccessToken:
    """TokenService 返回的访问令牌及客户端所需元数据。"""

    token: str
    token_type: str
    expires_in: int


class TokenService:
    """负责签发和验证带过期时间的 JWT Access Token。"""

    def __init__(
        self,
        secret_key: str,
        algorithm: str,
        access_token_expire_minutes: int,
    ) -> None:
        """保存签名配置，并拒绝长度不足的对称密钥。"""
        if len(secret_key.encode("utf-8")) < 32:
            raise ValueError("JWT 密钥至少需要 32 字节")
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes

    def create_access_token(
        self,
        user_id: UUID,
        expires_delta: timedelta | None = None,
    ) -> AccessToken:
        """把用户 UUID 写入 sub，并签发包含签发时间和过期时间的 Token。"""
        now = datetime.now(timezone.utc)
        lifetime = (
            expires_delta
            if expires_delta is not None
            else timedelta(minutes=self.access_token_expire_minutes)
        )
        expires_at = now + lifetime
        payload = {
            "sub": str(user_id),
            "type": "access",
            "iat": now,
            "exp": expires_at,
        }
        token = jwt.encode(
            payload,
            self.secret_key,
            algorithm=self.algorithm,
        )
        return AccessToken(
            token=token,
            token_type="bearer",
            expires_in=int(lifetime.total_seconds()),
        )

    def decode_user_id(self, token: str) -> UUID:
        """验证签名、类型和过期时间，并从 sub 恢复用户 UUID。"""
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"require": ["sub", "type", "iat", "exp"]},
            )
            if payload["type"] != "access":
                raise InvalidTokenError("Token 类型不是 access")
            return UUID(payload["sub"])
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise self.invalid_token_exception() from exc

    @staticmethod
    def invalid_token_exception() -> BusinessException:
        """构造符合 Bearer 认证协议的统一 Token 错误。"""
        return BusinessException(
            code=ErrorCode.INVALID_TOKEN,
            message="登录凭证无效或已过期",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
