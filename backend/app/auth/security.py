import hashlib

from pwdlib import PasswordHash

_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$xmHU+ZiRtkg4K+UFNbXhPQ$"
    "/0bMhUpYb7RoUVAHUUL5qCTQqmvZreMsabV8mSLEsR4"
)


class PasswordHasher:
    """封装 Argon2id 密码哈希、验证与参数升级判断。"""

    def __init__(self) -> None:
        """创建基于 pwdlib 推荐 Argon2id 参数的哈希器。"""
        self._hasher = PasswordHash.recommended()

    def hash(self, password: str) -> str:
        """生成新的不可逆 Argon2id 密码摘要。"""
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        """验证密码与摘要匹配；仅将布尔结果交给调用方。"""
        try:
            return self._hasher.verify(password, password_hash)
        except (ValueError, TypeError):
            return False

    def verify_and_update(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        """验证密码并按 pwdlib API 返回可选的新摘要。"""
        try:
            return self._hasher.verify_and_update(password, password_hash)
        except (ValueError, TypeError):
            return False, None


def normalize_username(username: str) -> str:
    """以统一规则清理用户名首尾空格并转为小写。"""
    return username.strip().lower()


def hash_bearer_token(raw_token: str) -> str:
    """将原始 Bearer Token 转为可索引的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
