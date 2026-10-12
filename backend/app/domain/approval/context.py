"""定义审批状态决策所需的不可变调用上下文。"""

from dataclasses import dataclass
from enum import StrEnum


class RejectTargetType(StrEnum):
    """标识审批驳回后应返回给申请人或前序审批节点。"""

    APPLICANT = "APPLICANT"
    PREVIOUS_NODE = "PREVIOUS_NODE"


@dataclass(frozen=True)
class TransitionContext:
    """携带状态机已解析的身份、节点和驳回目标信息。"""

    actor_id: str
    applicant_id: str
    current_assignee_ids: frozenset[str] = frozenset()
    is_first_approval_node: bool = False
    is_final_approval_node: bool = False
    reject_target_type: RejectTargetType | None = None
    target_node_code: str | None = None
    target_operator_id: str | None = None
