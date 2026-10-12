"""定义审批状态机可供调用方稳定识别的领域错误。"""

from enum import StrEnum


class ApprovalTransitionErrorCode(StrEnum):
    """列举审批状态决策的稳定错误代码。"""

    INVALID_STATUS_ACTION = "INVALID_STATUS_ACTION"
    ACTOR_NOT_APPLICANT = "ACTOR_NOT_APPLICANT"
    ACTOR_NOT_CURRENT_ASSIGNEE = "ACTOR_NOT_CURRENT_ASSIGNEE"
    WITHDRAW_NOT_FIRST_NODE = "WITHDRAW_NOT_FIRST_NODE"
    APPROVE_NODE_CONTEXT_INVALID = "APPROVE_NODE_CONTEXT_INVALID"
    REJECT_TARGET_REQUIRED = "REJECT_TARGET_REQUIRED"
    REJECT_TARGET_CONTEXT_INVALID = "REJECT_TARGET_CONTEXT_INVALID"
    VOID_RULE_NOT_ENABLED = "VOID_RULE_NOT_ENABLED"
    CREATE_NOT_STATE_TRANSITION = "CREATE_NOT_STATE_TRANSITION"


class ApprovalTransitionError(Exception):
    """携带稳定错误码的审批状态迁移领域异常。"""

    def __init__(self, code: ApprovalTransitionErrorCode, message: str) -> None:
        """保存机器可读错误码及便于排查的中文说明。"""
        self.code = code
        super().__init__(message)
