from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.core.security.password_hasher import PasswordHasher
from app.core.security.token_service import TokenService
from app.modules.identity.repository.user_repository import UserRepository
from app.modules.identity.schema.auth_schema import (
    TokenResponse,
    UserRegisterRequest,
    UserResponse,
)


# 用户不存在时仍执行一次真实格式的哈希校验，降低登录耗时暴露账号是否存在的风险。
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$wagCPXjifgvUFBzq4hqe3w$"
    "CYaIb8sB+wtD+Vu/P4uod1+Qof8h+1g7bbDlBID48Rc"
)


class AuthService:
    """编排用户注册、密码安全与认证事务。"""

    def __init__(
        self,
        db: AsyncSession,
        password_hasher: PasswordHasher | None = None,
        token_service: TokenService | None = None,
    ) -> None:
        """组装用户仓储和可替换的密码哈希组件。"""
        self.db = db
        self.repository = UserRepository(db)
        self.password_hasher = password_hasher or PasswordHasher()
        self.token_service = token_service

    async def register(self, request: UserRegisterRequest) -> UserResponse:
        """校验邮箱、哈希密码并在单个事务中创建用户。"""
        email = str(request.email)
        existing_user = await self.repository.get_by_email(email)
        if existing_user is not None:
            raise self._email_conflict()

        # Argon2 计算成本较高，仅在确定邮箱尚未注册后执行。
        password_hash = await self.password_hasher.hash(request.password)

        try:
            user = await self.repository.create(
                email=email,
                password_hash=password_hash,
            )
            await self.db.commit()
        except IntegrityError as exc:
            # 应用层预检查不能消除并发竞争，数据库唯一约束是最终防线。
            await self.db.rollback()
            raise self._email_conflict() from exc
        except Exception:
            await self.db.rollback()
            raise

        app_logger.info("用户注册成功 user_id=%s", user.id)
        return UserResponse.model_validate(user)

    async def login(self, email: str, password: str) -> TokenResponse:
        """校验邮箱、密码和账号状态，并签发 Access Token。"""
        normalized_email = email.strip().lower()
        user = await self.repository.get_by_email(normalized_email)

        if user is None:
            await self.password_hasher.verify(password, DUMMY_PASSWORD_HASH)
            raise self._invalid_credentials()

        password_is_valid = await self.password_hasher.verify(
            password,
            user.password_hash,
        )
        if not password_is_valid:
            raise self._invalid_credentials()

        if not user.is_active:
            raise BusinessException(
                code=ErrorCode.ACCOUNT_DISABLED,
                message="账号已停用",
                status_code=403,
            )

        if self.token_service is None:
            raise RuntimeError("登录服务缺少 TokenService")

        access_token = self.token_service.create_access_token(user.id)
        app_logger.info("用户登录成功 user_id=%s", user.id)
        return TokenResponse(
            access_token=access_token.token,
            token_type=access_token.token_type,
            expires_in=access_token.expires_in,
        )

    def _email_conflict(self) -> BusinessException:
        """构造不泄露数据库细节的重复邮箱业务异常。"""
        return BusinessException(
            code=ErrorCode.EMAIL_ALREADY_REGISTERED,
            message="该邮箱已注册",
            status_code=409,
        )

    def _invalid_credentials(self) -> BusinessException:
        """对不存在邮箱和错误密码返回完全相同的认证错误。"""
        return BusinessException(
            code=ErrorCode.INVALID_CREDENTIALS,
            message="邮箱或密码错误",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
