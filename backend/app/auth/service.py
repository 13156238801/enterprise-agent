import asyncio
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.auth.errors import AuthError, AuthErrorCode
from app.auth.repository import AuthRepository
from app.auth.schemas import LoginRequest, LoginResponse, PublicUser
from app.auth.security import (
    _DUMMY_PASSWORD_HASH,
    PasswordHasher,
    hash_bearer_token,
    normalize_username,
)
from app.core.config import Settings
from app.models.auth import AuthSession, User


class AuthService:
    """协调凭据校验、会话事务和可信身份构建。"""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        """绑定单请求 Session、运行配置及可替换的密码哈希器。"""
        self._session = session
        self._settings = settings
        self._password_hasher = password_hasher or PasswordHasher()
        self._repository = AuthRepository(session)

    async def login(self, login_request: LoginRequest) -> LoginResponse:
        """校验凭据并在单事务中创建会话，只将原 Token 返回一次。"""
        username = normalize_username(login_request.username)
        password = login_request.password.get_secret_value()
        async with self._session.begin():
            user = await self._repository.find_user_by_username(username)
            if user is None:
                await asyncio.to_thread(
                    self._password_hasher.verify, password, _DUMMY_PASSWORD_HASH
                )
                raise AuthError(AuthErrorCode.INVALID_CREDENTIALS)

            password_valid = await asyncio.to_thread(
                self._password_hasher.verify, password, user.password_hash
            )
            if not password_valid or not user.enabled:
                raise AuthError(AuthErrorCode.INVALID_CREDENTIALS)

            now = datetime.now(UTC)
            raw_token = secrets.token_urlsafe(32)
            auth_session = AuthSession(
                user_id=user.id,
                token_hash=hash_bearer_token(raw_token),
                created_at=now,
                expires_at=now + timedelta(minutes=self._settings.auth_session_ttl_minutes),
            )
            await self._repository.create_session(auth_session)
            expires_in = max(
                0, int((auth_session.expires_at - auth_session.created_at).total_seconds())
            )
            response_user = self._public_user(user)

        return LoginResponse(
            access_token=raw_token,
            expires_in=expires_in,
            user=response_user,
        )

    async def authenticate(self, raw_token: str) -> AuthContext:
        """解析原始 Token 并根据数据库 Session、User 和角色构建身份。"""
        async with self._session.begin():
            auth_session = await self._repository.resolve_session(hash_bearer_token(raw_token))
            if auth_session is None or auth_session.revoked_at is not None:
                raise AuthError(AuthErrorCode.UNAUTHENTICATED)
            if auth_session.expires_at <= datetime.now(UTC):
                raise AuthError(AuthErrorCode.TOKEN_EXPIRED)
            user = auth_session.user
            if not user.enabled:
                raise AuthError(AuthErrorCode.USER_DISABLED)

            return AuthContext(
                session_id=auth_session.id,
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                roles=frozenset(assignment.role for assignment in user.role_assignments),
            )

    async def logout(self, auth_context: AuthContext) -> None:
        """在独立事务中撤销当前可信上下文所指向的会话。"""
        async with self._session.begin():
            auth_session = await self._repository.find_session_by_id(auth_context.session_id)
            if auth_session is not None and auth_session.revoked_at is None:
                await self._repository.revoke_session(auth_session, datetime.now(UTC))

    @staticmethod
    def _public_user(user: User) -> PublicUser:
        """从 ORM 用户构建仅包含允许公开字段的响应对象。"""
        return PublicUser(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            roles=sorted(
                (assignment.role for assignment in user.role_assignments),
                key=lambda role: role.value,
            ),
        )
