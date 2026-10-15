"""定义 Submit API 使用的稳定审批错误。"""

from enum import StrEnum

from app.core.errors import AppError


class ApprovalErrorCode(StrEnum):
    """列举提交申请时可向客户端稳定公开的错误码。"""

    NOT_FOUND = "APPROVAL_NOT_FOUND"
    INVALID_TRANSITION = "APPROVAL_INVALID_TRANSITION"
    FORBIDDEN = "APPROVAL_FORBIDDEN"
    WORKFLOW_NOT_CONFIGURED = "APPROVAL_WORKFLOW_NOT_CONFIGURED"
    DATA_INTEGRITY_ERROR = "APPROVAL_DATA_INTEGRITY_ERROR"


_MESSAGES = {
    ApprovalErrorCode.NOT_FOUND: "审批申请不存在",
    ApprovalErrorCode.INVALID_TRANSITION: "当前审批状态不允许提交",
    ApprovalErrorCode.FORBIDDEN: "只有原申请人可以提交此审批",
    ApprovalErrorCode.WORKFLOW_NOT_CONFIGURED: "当前业务尚未配置可用审批流程",
    ApprovalErrorCode.DATA_INTEGRITY_ERROR: "审批申请或绑定流程数据不完整",
}


class ApprovalError(AppError):
    """携带审批稳定错误码并避免向客户端泄露内部异常。"""

    def __init__(self, code: ApprovalErrorCode) -> None:
        """按错误码设置固定公开消息。"""
        super().__init__(code.value, _MESSAGES[code])
