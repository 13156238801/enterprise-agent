"""提供审批领域类型与纯状态迁移规则。"""

from app.domain.approval.context import RejectTargetType, TransitionContext
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.approval.errors import ApprovalTransitionError, ApprovalTransitionErrorCode
from app.domain.approval.state_machine import TransitionDecision, decide_transition

__all__ = [
    "ApprovalAction",
    "ApprovalStatus",
    "ApprovalTransitionError",
    "ApprovalTransitionErrorCode",
    "BusinessType",
    "RejectTargetType",
    "TransitionContext",
    "TransitionDecision",
    "decide_transition",
]
