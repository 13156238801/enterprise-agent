"""审批状态机纯领域规则测试。"""

import pytest

from app.domain.approval.context import RejectTargetType, TransitionContext
from app.domain.approval.enums import ApprovalAction, ApprovalStatus
from app.domain.approval.errors import (
    ApprovalTransitionError,
    ApprovalTransitionErrorCode,
)
from app.domain.approval.state_machine import decide_transition


def _context(
    *,
    actor_id: str = "applicant-1",
    applicant_id: str = "applicant-1",
    current_assignee_ids: frozenset[str] = frozenset({"approver-1"}),
    is_first_approval_node: bool = False,
    is_final_approval_node: bool = False,
    reject_target_type: RejectTargetType | None = None,
    target_node_code: str | None = None,
    target_operator_id: str | None = None,
) -> TransitionContext:
    """构造状态机测试所需的不可变业务上下文。"""
    return TransitionContext(
        actor_id=actor_id,
        applicant_id=applicant_id,
        current_assignee_ids=current_assignee_ids,
        is_first_approval_node=is_first_approval_node,
        is_final_approval_node=is_final_approval_node,
        reject_target_type=reject_target_type,
        target_node_code=target_node_code,
        target_operator_id=target_operator_id,
    )


def _assert_error_code(
    status: ApprovalStatus,
    action: ApprovalAction,
    context: TransitionContext,
    expected_code: ApprovalTransitionErrorCode,
) -> None:
    """验证指定状态机输入抛出预期的稳定领域错误码。"""
    with pytest.raises(ApprovalTransitionError) as exc_info:
        decide_transition(status, action, context)
    assert exc_info.value.code is expected_code


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        (ApprovalAction.SAVE, ApprovalStatus.DRAFT),
        (ApprovalAction.SUBMIT, ApprovalStatus.IN_APPROVAL),
    ],
)
def test_applicant_can_save_or_submit_draft(
    action: ApprovalAction, expected_status: ApprovalStatus
) -> None:
    """验证申请人可保存草稿或将草稿提交审批。"""
    decision = decide_transition(ApprovalStatus.DRAFT, action, _context())
    assert decision.action is action
    assert decision.from_status is ApprovalStatus.DRAFT
    assert decision.to_status is expected_status


@pytest.mark.parametrize("action", [ApprovalAction.SAVE, ApprovalAction.SUBMIT])
def test_non_applicant_cannot_change_draft(action: ApprovalAction) -> None:
    """验证非申请人不能保存或提交草稿。"""
    _assert_error_code(
        ApprovalStatus.DRAFT,
        action,
        _context(actor_id="other-user"),
        ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT,
    )


@pytest.mark.parametrize(
    "action", [ApprovalAction.APPROVE, ApprovalAction.REJECT, ApprovalAction.WITHDRAW]
)
def test_draft_rejects_approval_actions(action: ApprovalAction) -> None:
    """验证草稿状态不接受审批、驳回或撤回动作。"""
    _assert_error_code(
        ApprovalStatus.DRAFT,
        action,
        _context(),
        ApprovalTransitionErrorCode.INVALID_STATUS_ACTION,
    )


@pytest.mark.parametrize(
    ("is_first", "is_final", "expected_status"),
    [
        (False, False, ApprovalStatus.IN_APPROVAL),
        (True, False, ApprovalStatus.IN_APPROVAL),
        (False, True, ApprovalStatus.COMPLETED),
        (True, True, ApprovalStatus.COMPLETED),
    ],
)
def test_current_approver_can_approve_intermediate_or_final_node(
    is_first: bool, is_final: bool, expected_status: ApprovalStatus
) -> None:
    """验证审批结果由最终节点标记决定，单节点流程也可同时为首尾节点。"""
    context = _context(
        actor_id="approver-1",
        is_first_approval_node=is_first,
        is_final_approval_node=is_final,
    )
    decision = decide_transition(ApprovalStatus.IN_APPROVAL, ApprovalAction.APPROVE, context)
    assert decision.to_status is expected_status


def test_non_current_assignee_cannot_approve() -> None:
    """验证不属于当前节点处理人集合的操作者不能审批。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.APPROVE,
        _context(actor_id="other-user", is_final_approval_node=True),
        ApprovalTransitionErrorCode.ACTOR_NOT_CURRENT_ASSIGNEE,
    )


def test_non_current_assignee_cannot_reject() -> None:
    """验证不属于当前节点处理人集合的操作者不能驳回。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.REJECT,
        _context(actor_id="other-user"),
        ApprovalTransitionErrorCode.ACTOR_NOT_CURRENT_ASSIGNEE,
    )


@pytest.mark.parametrize(
    ("target", "expected_status"),
    [
        (RejectTargetType.APPLICANT, ApprovalStatus.DRAFT),
        (RejectTargetType.PREVIOUS_NODE, ApprovalStatus.IN_APPROVAL),
    ],
)
def test_current_approver_can_reject_to_valid_target(
    target: RejectTargetType, expected_status: ApprovalStatus
) -> None:
    """验证两种合法驳回目标对应的状态结果。"""
    context = _context(
        actor_id="approver-1",
        reject_target_type=target,
        target_node_code="manager_review" if target is RejectTargetType.PREVIOUS_NODE else None,
        target_operator_id="approver-0" if target is RejectTargetType.PREVIOUS_NODE else None,
    )
    decision = decide_transition(ApprovalStatus.IN_APPROVAL, ApprovalAction.REJECT, context)
    assert decision.to_status is expected_status


@pytest.mark.parametrize(
    ("target_node_code", "target_operator_id"),
    [(None, "approver-0"), ("manager_review", None)],
)
def test_reject_to_previous_node_requires_complete_target_context(
    target_node_code: str | None, target_operator_id: str | None
) -> None:
    """验证驳回到前序节点时必须同时指定节点和处理人。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.REJECT,
        _context(
            actor_id="approver-1",
            reject_target_type=RejectTargetType.PREVIOUS_NODE,
            target_node_code=target_node_code,
            target_operator_id=target_operator_id,
        ),
        ApprovalTransitionErrorCode.REJECT_TARGET_CONTEXT_INVALID,
    )


def test_reject_requires_target_type() -> None:
    """验证驳回动作缺少目标类型时返回稳定错误码。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.REJECT,
        _context(actor_id="approver-1"),
        ApprovalTransitionErrorCode.REJECT_TARGET_REQUIRED,
    )


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (status, action)
        for status in (ApprovalStatus.COMPLETED, ApprovalStatus.VOIDED)
        for action in (
            ApprovalAction.SAVE,
            ApprovalAction.SUBMIT,
            ApprovalAction.APPROVE,
            ApprovalAction.REJECT,
            ApprovalAction.WITHDRAW,
        )
    ],
)
def test_terminal_status_rejects_all_supported_transitions(
    status: ApprovalStatus, action: ApprovalAction
) -> None:
    """验证已完成和已作废状态拒绝所有普通状态迁移动作。"""
    _assert_error_code(
        status,
        action,
        _context(actor_id="approver-1", is_first_approval_node=True),
        ApprovalTransitionErrorCode.INVALID_STATUS_ACTION,
    )


@pytest.mark.parametrize(
    ("action", "expected_code"),
    [
        (ApprovalAction.CREATE, ApprovalTransitionErrorCode.CREATE_NOT_STATE_TRANSITION),
        (ApprovalAction.VOID, ApprovalTransitionErrorCode.VOID_RULE_NOT_ENABLED),
    ],
)
def test_create_and_void_are_rejected_as_special_actions(
    action: ApprovalAction, expected_code: ApprovalTransitionErrorCode
) -> None:
    """验证 CREATE 与 VOID 优先按未支持的特殊动作处理。"""
    _assert_error_code(
        ApprovalStatus.DRAFT,
        action,
        _context(actor_id="not-applicant"),
        expected_code,
    )


def test_withdraw_by_applicant_at_first_node_returns_to_draft() -> None:
    """验证申请人可在首个审批节点撤回并回到草稿。"""
    decision = decide_transition(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.WITHDRAW,
        _context(is_first_approval_node=True),
    )
    assert decision.to_status is ApprovalStatus.DRAFT


def test_withdraw_after_first_node_is_rejected() -> None:
    """验证审批已离开首节点后申请人不能撤回。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.WITHDRAW,
        _context(is_first_approval_node=False),
        ApprovalTransitionErrorCode.WITHDRAW_NOT_FIRST_NODE,
    )


def test_non_applicant_cannot_withdraw_at_first_node() -> None:
    """验证首节点的撤回权限仍只属于申请人本人。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.WITHDRAW,
        _context(actor_id="approver-1", is_first_approval_node=True),
        ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT,
    )


def test_withdraw_actor_error_precedes_first_node_context_error() -> None:
    """验证撤回时申请人身份错误优先于非首节点上下文错误。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.WITHDRAW,
        _context(actor_id="approver-1", is_first_approval_node=False),
        ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT,
    )


def test_reject_actor_error_precedes_missing_target_error() -> None:
    """验证驳回时当前处理人身份错误优先于缺少驳回目标。"""
    _assert_error_code(
        ApprovalStatus.IN_APPROVAL,
        ApprovalAction.REJECT,
        _context(actor_id="other-user", reject_target_type=None),
        ApprovalTransitionErrorCode.ACTOR_NOT_CURRENT_ASSIGNEE,
    )


@pytest.mark.parametrize(
    ("status", "action", "context", "expected_code"),
    [
        (
            ApprovalStatus.COMPLETED,
            ApprovalAction.SUBMIT,
            _context(actor_id="not-applicant"),
            ApprovalTransitionErrorCode.INVALID_STATUS_ACTION,
        ),
        (
            ApprovalStatus.DRAFT,
            ApprovalAction.APPROVE,
            _context(actor_id="not-assignee"),
            ApprovalTransitionErrorCode.INVALID_STATUS_ACTION,
        ),
        (
            ApprovalStatus.IN_APPROVAL,
            ApprovalAction.APPROVE,
            _context(actor_id="not-assignee"),
            ApprovalTransitionErrorCode.ACTOR_NOT_CURRENT_ASSIGNEE,
        ),
    ],
)
def test_error_precedence_is_status_before_actor(
    status: ApprovalStatus,
    action: ApprovalAction,
    context: TransitionContext,
    expected_code: ApprovalTransitionErrorCode,
) -> None:
    """验证状态合法性先于申请人或审批人身份校验。"""
    _assert_error_code(status, action, context, expected_code)


def test_same_transition_input_returns_equal_decision() -> None:
    """验证相同状态、动作和上下文始终得到相等的决策结果。"""
    context = _context(actor_id="approver-1", is_final_approval_node=True)
    first = decide_transition(ApprovalStatus.IN_APPROVAL, ApprovalAction.APPROVE, context)
    second = decide_transition(ApprovalStatus.IN_APPROVAL, ApprovalAction.APPROVE, context)
    assert first == second


def test_domain_modules_do_not_import_infrastructure_or_frameworks() -> None:
    """验证状态机领域模块不依赖 ORM、Web 框架或基础设施模块。"""
    import ast
    from pathlib import Path

    source_root = Path(__file__).parents[1] / "app" / "domain" / "approval"
    forbidden_roots = {"fastapi", "sqlalchemy", "redis", "app.models", "app.infrastructure"}
    for source_path in source_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = {node.module}
            else:
                continue
            assert not any(
                name == root or name.startswith(f"{root}.")
                for name in imported
                for root in forbidden_roots
            )
