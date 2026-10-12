"""依据纯领域输入判断审批动作是否合法及其目标状态。"""

from dataclasses import dataclass

from app.domain.approval.context import RejectTargetType, TransitionContext
from app.domain.approval.enums import ApprovalAction, ApprovalStatus
from app.domain.approval.errors import ApprovalTransitionError, ApprovalTransitionErrorCode


@dataclass(frozen=True)
class TransitionDecision:
    """表示一次已通过校验的审批状态迁移决策。"""

    action: ApprovalAction
    from_status: ApprovalStatus
    to_status: ApprovalStatus


_ALLOWED_ACTIONS: dict[ApprovalStatus, frozenset[ApprovalAction]] = {
    ApprovalStatus.DRAFT: frozenset({ApprovalAction.SAVE, ApprovalAction.SUBMIT}),
    ApprovalStatus.IN_APPROVAL: frozenset(
        {ApprovalAction.APPROVE, ApprovalAction.REJECT, ApprovalAction.WITHDRAW}
    ),
    ApprovalStatus.COMPLETED: frozenset(),
    ApprovalStatus.VOIDED: frozenset(),
}


def decide_transition(
    current_status: ApprovalStatus,
    action: ApprovalAction,
    context: TransitionContext,
) -> TransitionDecision:
    """按特殊动作、状态、操作者和动作上下文的固定顺序决策迁移。"""
    if action is ApprovalAction.CREATE:
        raise ApprovalTransitionError(
            ApprovalTransitionErrorCode.CREATE_NOT_STATE_TRANSITION,
            "CREATE 是创建审计动作，不是已有申请的状态迁移。",
        )
    if action is ApprovalAction.VOID:
        raise ApprovalTransitionError(
            ApprovalTransitionErrorCode.VOID_RULE_NOT_ENABLED,
            "作废规则尚未启用。",
        )

    if action not in _ALLOWED_ACTIONS.get(current_status, frozenset()):
        raise ApprovalTransitionError(
            ApprovalTransitionErrorCode.INVALID_STATUS_ACTION,
            "当前审批状态不允许该动作。",
        )

    if action in {ApprovalAction.SAVE, ApprovalAction.SUBMIT, ApprovalAction.WITHDRAW}:
        if context.actor_id != context.applicant_id:
            raise ApprovalTransitionError(
                ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT,
                "该动作仅允许申请人本人执行。",
            )
    elif action in {ApprovalAction.APPROVE, ApprovalAction.REJECT}:
        if context.actor_id not in context.current_assignee_ids:
            raise ApprovalTransitionError(
                ApprovalTransitionErrorCode.ACTOR_NOT_CURRENT_ASSIGNEE,
                "操作者不是当前审批节点处理人。",
            )

    if action is ApprovalAction.SAVE:
        next_status = ApprovalStatus.DRAFT
    elif action is ApprovalAction.SUBMIT:
        next_status = ApprovalStatus.IN_APPROVAL
    elif action is ApprovalAction.APPROVE:
        next_status = (
            ApprovalStatus.COMPLETED
            if context.is_final_approval_node
            else ApprovalStatus.IN_APPROVAL
        )
    elif action is ApprovalAction.REJECT:
        next_status = _reject_target_status(context)
    else:
        if not context.is_first_approval_node:
            raise ApprovalTransitionError(
                ApprovalTransitionErrorCode.WITHDRAW_NOT_FIRST_NODE,
                "仅当前仍在首个审批节点时允许申请人撤回。",
            )
        next_status = ApprovalStatus.DRAFT

    return TransitionDecision(action, current_status, next_status)


def _reject_target_status(context: TransitionContext) -> ApprovalStatus:
    """校验驳回目标上下文并返回对应状态，前序节点驳回保持审批中。"""
    target = context.reject_target_type
    if target is None:
        raise ApprovalTransitionError(
            ApprovalTransitionErrorCode.REJECT_TARGET_REQUIRED,
            "驳回动作必须指定目标类型。",
        )
    if target is RejectTargetType.APPLICANT:
        return ApprovalStatus.DRAFT
    if target is RejectTargetType.PREVIOUS_NODE:
        if not context.target_node_code or not context.target_operator_id:
            raise ApprovalTransitionError(
                ApprovalTransitionErrorCode.REJECT_TARGET_CONTEXT_INVALID,
                "驳回到前序节点时必须指定节点和处理人。",
            )
        return ApprovalStatus.IN_APPROVAL
    raise ApprovalTransitionError(
        ApprovalTransitionErrorCode.REJECT_TARGET_CONTEXT_INVALID,
        "驳回目标类型无效。",
    )
