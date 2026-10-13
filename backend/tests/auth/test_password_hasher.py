"""密码哈希安全行为测试。"""

from app.auth.security import _DUMMY_PASSWORD_HASH, PasswordHasher, normalize_username
from app.domain.auth.enums import UserRole


def test_argon2_hash_verifies_only_the_original_password() -> None:
    """验证 Argon2id 密码哈希可验证原密码且不保存明文。"""
    hasher = PasswordHasher()
    password_hash = hasher.hash("local-demo-password")

    assert password_hash != "local-demo-password"
    assert hasher.verify("local-demo-password", password_hash)
    assert not hasher.verify("wrong-password", password_hash)
    assert password_hash != hasher.hash("local-demo-password")


def test_argon2_verify_and_update_reports_current_hash_without_rehash() -> None:
    """验证密码匹配时哈希器正确报告是否需要升级摘要。"""
    hasher = PasswordHasher()
    password_hash = hasher.hash("local-demo-password")

    assert hasher.verify_and_update("local-demo-password", password_hash) == (True, None)
    assert hasher.verify_and_update("wrong-password", password_hash) == (False, None)


def test_username_normalization_strips_and_lowercases() -> None:
    """验证用户名统一去除首尾空白并转为小写。"""
    assert normalize_username(" Manager ") == "manager"


def test_fixed_dummy_hash_is_valid_argon2id_hash() -> None:
    """验证未知用户防枚举使用固定、可验证的 Argon2id 摘要。"""
    assert not PasswordHasher().verify("any-user-password", _DUMMY_PASSWORD_HASH)


def test_user_role_enum_contains_only_approved_roles() -> None:
    """验证认证领域仅定义四种已批准角色，不增加隐式管理角色。"""
    assert [role.value for role in UserRole] == [
        "APPLICANT",
        "APPROVER",
        "DATA_ADMIN",
        "ADMIN",
    ]

