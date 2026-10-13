from enum import StrEnum

from app.core.errors import AppError


class AuthErrorCode(StrEnum):
    """列出认证和授权 API 使用的稳定错误码。"""

    INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    UNAUTHENTICATED = "AUTH_UNAUTHENTICATED"
    TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    USER_DISABLED = "AUTH_USER_DISABLED"
    PERMISSION_DENIED = "AUTH_PERMISSION_DENIED"


class AuthError(AppError):
    """表示可安全返回给认证 API 客户端的认证业务错误。"""

    def __init__(self, code: AuthErrorCode) -> None:
        """根据稳定认证错误码设置固定公开消息。"""
        messages = {
            AuthErrorCode.INVALID_CREDENTIALS: "用户名或密码错误",
            AuthErrorCode.UNAUTHENTICATED: "需要有效的认证凭据",
            AuthErrorCode.TOKEN_EXPIRED: "认证会话已过期",
            AuthErrorCode.USER_DISABLED: "用户账号不可用",
            AuthErrorCode.PERMISSION_DENIED: "无权执行此操作",
        }
        super().__init__(code.value, messages[code])
