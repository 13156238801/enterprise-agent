from dataclasses import dataclass
from uuid import UUID

from app.domain.auth.enums import UserRole


@dataclass(frozen=True)
class AuthContext:
    """保存服务端解析出的可信身份，不携带密码或 Token 等敏感值。"""

    session_id: UUID
    user_id: UUID
    username: str
    display_name: str
    roles: frozenset[UserRole]
