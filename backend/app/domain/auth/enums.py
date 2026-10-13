from enum import StrEnum


class UserRole(StrEnum):
    """定义由服务端授予的认证角色；ADMIN 不隐式拥有业务角色。"""

    APPLICANT = "APPLICANT"
    APPROVER = "APPROVER"
    DATA_ADMIN = "DATA_ADMIN"
    ADMIN = "ADMIN"
