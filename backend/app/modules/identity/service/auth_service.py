from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exception.business_exception import BusinessException
from app.common.exception.error_code import ErrorCode
from app.core.loggers import app_logger
from app.core.security.password_hasher import PasswordHasher
from app.modules.identity.repository.user_repository import UserRepository
from app.modules.identity.schema.auth_schema import UserRegisterRequest, UserResponse


class AuthService:
    """编排用户注册、密码安全与认证事务。"""

    def __init__(
        self,
        db: AsyncSession,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        """组装用户仓储和可替换的密码哈希组件。"""
        self.db = db
        self.repository = UserRepository(db)
        self.password_hasher = password_hasher or PasswordHasher()

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

    def _email_conflict(self) -> BusinessException:
        """构造不泄露数据库细节的重复邮箱业务异常。"""
        return BusinessException(
            code=ErrorCode.EMAIL_ALREADY_REGISTERED,
            message="该邮箱已注册",
            status_code=409,
        )
