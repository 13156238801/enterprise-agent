from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr

from app.domain.auth.enums import UserRole


class LoginRequest(BaseModel):
    """验证登录输入并避免密码在模型展示时暴露。"""

    model_config = ConfigDict(extra="forbid")

    username: str
    password: SecretStr


class PublicUser(BaseModel):
    """定义认证 API 可公开返回的用户字段。"""

    id: UUID
    username: str
    display_name: str
    roles: list[UserRole]


class LoginResponse(BaseModel):
    """定义登录成功后一次性返回的原始 Token 和真实 TTL。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: PublicUser
