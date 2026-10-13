from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.auth import AuthSession, User, UserRoleAssignment


class AuthRepository:
    """执行认证数据查询和 flush，不拥有事务提交或回滚。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求独享的数据库 Session。"""
        self._session = session

    async def find_user_by_username(self, username: str) -> User | None:
        """按已归一化用户名加载用户及角色集合，避免后续重复查询。"""
        statement = (
            select(User).options(joinedload(User.role_assignments)).where(User.username == username)
        )
        result = await self._session.execute(statement)
        return result.unique().scalar_one_or_none()

    async def create_session(self, auth_session: AuthSession) -> AuthSession:
        """加入并 flush 新会话以获得应用侧 ID，不提交事务。"""
        self._session.add(auth_session)
        await self._session.flush()
        return auth_session

    async def resolve_session(self, token_hash: str) -> AuthSession | None:
        """一次查询加载 Session、User 和角色，保证请求身份读取一致。"""
        statement = (
            select(AuthSession)
            .options(
                joinedload(AuthSession.user).joinedload(User.role_assignments),
            )
            .where(AuthSession.token_hash == token_hash)
        )
        result = await self._session.execute(statement)
        return result.unique().scalar_one_or_none()

    async def find_session_by_id(self, session_id: UUID) -> AuthSession | None:
        """按可信 AuthContext 中的会话 ID 查找待撤销记录。"""
        return await self._session.get(AuthSession, session_id)

    async def revoke_session(self, auth_session: AuthSession, revoked_at: datetime) -> None:
        """更新当前会话撤销时间并 flush，不在仓储层控制事务。"""
        auth_session.revoked_at = revoked_at
        await self._session.flush()

    async def list_user_roles(self, user_id: UUID) -> list[UserRoleAssignment]:
        """读取用户角色分配；主要供显式用户角色应用场景复用。"""
        result = await self._session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id)
        )
        return list(result.scalars().all())
