from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.auth.errors import AuthError, AuthErrorCode
from app.auth.service import AuthService
from app.domain.auth.enums import UserRole
from app.master_data.service import MasterDataService

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """为每个 HTTP 请求创建并关闭独立 AsyncSession。"""
    async with request.app.state.db_session_factory() as session:
        yield session


async def get_auth_service(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AuthService:
    """使用请求 Session 和应用设置构造认证服务。"""
    return AuthService(session, request.app.state.settings)


async def get_master_data_service(
    session: AsyncSession = Depends(get_db_session),
) -> MasterDataService:
    """用当前请求独享的 Session 构造主数据应用服务。"""
    return MasterDataService(session)


async def get_current_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthContext:
    """解析 Bearer 凭据并返回由数据库构建的可信身份。"""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthError(AuthErrorCode.UNAUTHENTICATED)
    return await auth_service.authenticate(credentials.credentials)


def require_any_role(*required_roles: UserRole) -> Callable[..., AuthContext]:
    """创建显式 OR 角色检查依赖，ADMIN 不自动满足其他业务角色。"""

    async def dependency(
        auth_context: AuthContext = Depends(get_current_auth),
    ) -> AuthContext:
        """检查认证上下文是否至少包含一个明确要求的角色。"""
        if not auth_context.roles.intersection(required_roles):
            raise AuthError(AuthErrorCode.PERMISSION_DENIED)
        return auth_context

    return dependency
