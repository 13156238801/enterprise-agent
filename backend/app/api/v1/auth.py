from fastapi import APIRouter, Depends, Response

from app.api.deps import get_auth_service, get_current_auth
from app.auth.context import AuthContext
from app.auth.schemas import LoginRequest, LoginResponse, PublicUser
from app.auth.service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """校验用户名和密码并返回不缓存的 Bearer Token。"""
    response.headers["Cache-Control"] = "no-store"
    return await auth_service.login(payload)


@router.get("/me", response_model=PublicUser)
async def current_user(auth_context: AuthContext = Depends(get_current_auth)) -> PublicUser:
    """返回当前服务端认证身份允许公开的用户与角色字段。"""
    return PublicUser(
        id=auth_context.user_id,
        username=auth_context.username,
        display_name=auth_context.display_name,
        roles=sorted(auth_context.roles, key=lambda role: role.value),
    )


@router.post("/logout", status_code=204)
async def logout(
    auth_context: AuthContext = Depends(get_current_auth),
    auth_service: AuthService = Depends(get_auth_service),
) -> Response:
    """撤销当前认证上下文绑定的会话并返回空响应。"""
    await auth_service.logout(auth_context)
    return Response(status_code=204)
