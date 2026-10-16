"""定义幂等基础设施稳定错误码。"""

from enum import StrEnum

from app.core.errors import AppError


class IdempotencyErrorCode(StrEnum):
    """列举幂等键校验、冲突和持久化完整性错误。"""

    KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    KEY_INVALID = "IDEMPOTENCY_KEY_INVALID"
    CONFLICT = "IDEMPOTENCY_CONFLICT"
    DATA_INTEGRITY_ERROR = "IDEMPOTENCY_DATA_INTEGRITY_ERROR"


_MESSAGES = {
    IdempotencyErrorCode.KEY_REQUIRED: "请求缺少 Idempotency-Key",
    IdempotencyErrorCode.KEY_INVALID: "Idempotency-Key 不合法",
    IdempotencyErrorCode.CONFLICT: "Idempotency-Key 已用于不同请求",
    IdempotencyErrorCode.DATA_INTEGRITY_ERROR: "幂等结果数据不完整",
}


class IdempotencyError(AppError):
    """携带稳定错误代码和安全公开消息的幂等异常。"""

    def __init__(self, code: IdempotencyErrorCode) -> None:
        """按幂等错误码构造不含请求内容的公开错误。"""
        super().__init__(code.value, _MESSAGES[code])
