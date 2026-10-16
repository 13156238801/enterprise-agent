"""协调审批提交、同意、驳回状态决策及流程运行时历史写入。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.schemas import ApprovalActionResponse, SubmitApprovalResponse
from app.auth.context import AuthContext
from app.domain.approval.context import RejectTargetType, TransitionContext
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.approval.errors import ApprovalTransitionError, ApprovalTransitionErrorCode
from app.domain.approval.state_machine import decide_transition
from app.domain.workflow.definition import WorkflowDefinitionSpec
from app.domain.workflow.errors import WorkflowDefinitionError
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.workflow.repository import WorkflowDefinitionRepository


class ApprovalService:
    """在一个数据库事务中完成审批申请的首次提交或重新提交。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定请求独享会话并构造不拥有事务边界的 Repository。"""
        self._session = session
        self._repository = ApprovalRepository(session)
        self._workflow_repository = WorkflowDefinitionRepository(session)

    async def submit(self, approval_id: UUID, auth_context: AuthContext) -> SubmitApprovalResponse:
        """验证并提交申请，绑定流程版本、初始化首节点并追加 SUBMIT 历史。"""
        async with self._session.begin():
            approval = await self._repository.get_application_for_action(approval_id)
            if approval is None:
                raise ApprovalError(ApprovalErrorCode.NOT_FOUND)
            change = approval.master_data_change
            if (
                approval.business_type is not BusinessType.MASTER_DATA_CHANGE
                or change is None
                or approval.business_id != str(change.id)
            ):
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)

            try:
                decision = decide_transition(
                    current_status=approval.status,
                    action=ApprovalAction.SUBMIT,
                    context=TransitionContext(
                        actor_id=str(auth_context.user_id),
                        applicant_id=approval.applicant_id,
                    ),
                )
            except ApprovalTransitionError as error:
                if error.code is ApprovalTransitionErrorCode.INVALID_STATUS_ACTION:
                    raise ApprovalError(ApprovalErrorCode.INVALID_TRANSITION) from error
                if error.code is ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT:
                    raise ApprovalError(ApprovalErrorCode.FORBIDDEN) from error
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR) from error

            code, version, spec = await self._resolve_workflow(approval, change.entity_type)
            first_node = spec.first_node()
            previous_task_id = approval.current_task_id
            previous_node_code = approval.current_node_code
            previous_node_name = approval.current_node_name
            workflow_instance_id = str(uuid4())
            current_task_id = str(uuid4())
            submitted_at = datetime.now(UTC)

            approval.workflow_code = code
            approval.workflow_version = version
            approval.status = decision.to_status
            approval.workflow_instance_id = workflow_instance_id
            approval.current_task_id = current_task_id
            approval.current_node_code = first_node.key
            approval.current_node_name = None
            approval.submitted_at = submitted_at
            approval.completed_at = None

            await self._repository.save_transition(
                ApprovalRecord(
                    approval_id=approval.id,
                    operator_id=str(auth_context.user_id),
                    action=decision.action,
                    from_status=decision.from_status,
                    to_status=decision.to_status,
                    from_task_id=previous_task_id,
                    from_node_code=previous_node_code,
                    from_node_name=previous_node_name,
                    target_task_id=current_task_id,
                    target_node_code=first_node.key,
                    target_node_name=None,
                    target_operator_id=None,
                )
            )

            return SubmitApprovalResponse(
                approval_id=approval.id,
                status=decision.to_status,
                workflow_code=code,
                workflow_version=version,
                workflow_instance_id=workflow_instance_id,
                current_task_id=current_task_id,
                current_node_code=first_node.key,
                current_node_role=first_node.role,
                submitted_at=submitted_at,
            )

    async def _resolve_workflow(
        self, approval: ApprovalApplication, entity_type: str
    ) -> tuple[str, int, WorkflowDefinitionSpec]:
        """读取已绑定精确版本或按显式业务映射选择当前 active 版本。"""
        code = approval.workflow_code
        version = approval.workflow_version
        if (code is None) != (version is None):
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        try:
            if code is not None and version is not None:
                spec = await self._workflow_repository.get_by_code_version(code, version)
                if spec is None:
                    raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
                return code, version, spec

            if approval.business_type is not BusinessType.MASTER_DATA_CHANGE:
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            if entity_type != "supplier":
                raise ApprovalError(ApprovalErrorCode.WORKFLOW_NOT_CONFIGURED)
            code = "supplier_change"
            spec = await self._workflow_repository.get_active(code)
            if spec is None:
                raise ApprovalError(ApprovalErrorCode.WORKFLOW_NOT_CONFIGURED)
            return code, spec.version, spec
        except WorkflowDefinitionError as error:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR) from error

    async def approve(
        self, approval_id: UUID, auth_context: AuthContext, comment: object = None
    ) -> ApprovalActionResponse:
        """在单一事务中执行 APPROVE 状态迁移并追加不可变审批历史。"""
        normalized_comment = _normalize_approve_comment(comment)
        async with self._session.begin():
            approval, spec, current_node, assignment = await self._load_action_context(
                approval_id, ApprovalAction.APPROVE, auth_context
            )
            actor_id = str(auth_context.user_id)
            eligible = current_node.role in auth_context.roles and (
                assignment.target_operator_id is None or assignment.target_operator_id == actor_id
            )
            context = TransitionContext(
                actor_id=actor_id,
                applicant_id=approval.applicant_id,
                current_assignee_ids=frozenset({actor_id}) if eligible else frozenset(),
                is_first_approval_node=spec.is_first_node(current_node.key),
                is_final_approval_node=spec.is_final_node(current_node.key),
            )
            decision = self._decide(approval.status, ApprovalAction.APPROVE, context)
            old_task_id = approval.current_task_id
            old_node_code = approval.current_node_code
            old_node_name = approval.current_node_name
            target_task_id: str | None = None
            target_node = None

            if decision.to_status is ApprovalStatus.COMPLETED:
                approval.current_task_id = None
                approval.current_node_code = None
                approval.current_node_name = None
                approval.completed_at = datetime.now(UTC)
            else:
                target_node = spec.next_node(current_node.key)
                if target_node is None:
                    raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
                target_task_id = str(uuid4())
                approval.current_task_id = target_task_id
                approval.current_node_code = target_node.key
                approval.current_node_name = None
                approval.completed_at = None

            approval.status = decision.to_status
            await self._repository.save_transition(
                ApprovalRecord(
                    approval_id=approval.id,
                    operator_id=actor_id,
                    action=decision.action,
                    comment=normalized_comment,
                    from_status=decision.from_status,
                    to_status=decision.to_status,
                    from_task_id=old_task_id,
                    from_node_code=old_node_code,
                    from_node_name=old_node_name,
                    target_task_id=target_task_id,
                    target_node_code=target_node.key if target_node is not None else None,
                    target_node_name=None,
                    target_operator_id=None,
                )
            )
            return _action_response(approval, decision.action, target_node)

    async def reject(
        self,
        approval_id: UUID,
        auth_context: AuthContext,
        target: object,
        comment: object,
    ) -> ApprovalActionResponse:
        """解析可信驳回目标后执行 REJECT 决策并原子更新运行时与历史。"""
        reject_target, normalized_comment = _normalize_reject_request(target, comment)
        async with self._session.begin():
            approval, spec, current_node, assignment = await self._load_action_context(
                approval_id, ApprovalAction.REJECT, auth_context
            )
            target_node = None
            target_operator_id = None
            if reject_target is RejectTargetType.APPLICANT:
                target_operator_id = approval.applicant_id
            else:
                target_node = spec.previous_node(current_node.key)
                if target_node is not None:
                    previous_record = await self._repository.find_previous_node_approver(
                        approval.id, target_node.key, current_node.key
                    )
                    if previous_record is None or not previous_record.operator_id:
                        raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
                    target_operator_id = previous_record.operator_id

            actor_id = str(auth_context.user_id)
            eligible = current_node.role in auth_context.roles and (
                assignment.target_operator_id is None or assignment.target_operator_id == actor_id
            )
            context = TransitionContext(
                actor_id=actor_id,
                applicant_id=approval.applicant_id,
                current_assignee_ids=frozenset({actor_id}) if eligible else frozenset(),
                is_first_approval_node=spec.is_first_node(current_node.key),
                is_final_approval_node=spec.is_final_node(current_node.key),
                reject_target_type=reject_target,
                target_node_code=target_node.key if target_node is not None else None,
                target_operator_id=target_operator_id,
            )
            try:
                decision = decide_transition(
                    current_status=approval.status,
                    action=ApprovalAction.REJECT,
                    context=context,
                )
            except ApprovalTransitionError as error:
                if (
                    error.code is ApprovalTransitionErrorCode.REJECT_TARGET_CONTEXT_INVALID
                    and reject_target is RejectTargetType.PREVIOUS_NODE
                    and target_node is None
                ):
                    raise ApprovalError(ApprovalErrorCode.REJECT_TARGET_UNAVAILABLE) from error
                self._raise_transition_error(error)

            old_task_id = approval.current_task_id
            old_node_code = approval.current_node_code
            old_node_name = approval.current_node_name
            target_task_id = None
            if reject_target is RejectTargetType.APPLICANT:
                approval.current_task_id = None
                approval.current_node_code = None
                approval.current_node_name = None
                approval.completed_at = None
            else:
                if target_node is None or target_operator_id is None:
                    raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
                target_task_id = str(uuid4())
                approval.current_task_id = target_task_id
                approval.current_node_code = target_node.key
                approval.current_node_name = None
                approval.completed_at = None
            approval.status = decision.to_status

            await self._repository.save_transition(
                ApprovalRecord(
                    approval_id=approval.id,
                    operator_id=actor_id,
                    action=decision.action,
                    comment=normalized_comment,
                    from_status=decision.from_status,
                    to_status=decision.to_status,
                    from_task_id=old_task_id,
                    from_node_code=old_node_code,
                    from_node_name=old_node_name,
                    target_task_id=target_task_id,
                    target_node_code=target_node.key if target_node is not None else None,
                    target_node_name=None,
                    target_operator_id=target_operator_id,
                )
            )
            return _action_response(approval, decision.action, target_node)

    async def _load_action_context(
        self,
        approval_id: UUID,
        action: ApprovalAction,
        auth_context: AuthContext,
    ):
        """验证业务关联并解析动作上下文；缺少运行时前先由状态机判定动作合法性。"""
        approval = await self._repository.get_application_for_action(approval_id)
        if approval is None:
            raise ApprovalError(ApprovalErrorCode.NOT_FOUND)
        change = approval.master_data_change
        if (
            approval.business_type is not BusinessType.MASTER_DATA_CHANGE
            or change is None
            or approval.business_id != str(change.id)
        ):
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        code, version = approval.workflow_code, approval.workflow_version
        if (
            not code
            or version is None
            or not approval.workflow_instance_id
            or not approval.current_task_id
            or not approval.current_node_code
        ):
            # DRAFT / COMPLETED 本就没有当前任务；先让唯一状态机拒绝非法动作。
            # 该合成上下文只用于通过状态与动作校验，不用于授权；IN_APPROVAL
            # 后续仍会加载真实任务和处理人，再以完整上下文做最终决策。
            actor_id = str(auth_context.user_id)
            self._decide(
                approval.status,
                action,
                TransitionContext(
                    actor_id=actor_id,
                    applicant_id=approval.applicant_id,
                    current_assignee_ids=frozenset({actor_id}),
                    reject_target_type=(
                        RejectTargetType.APPLICANT if action is ApprovalAction.REJECT else None
                    ),
                ),
            )
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        try:
            spec = await self._workflow_repository.get_by_code_version(code, version)
            if spec is None:
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            current_node = spec.get_node(approval.current_node_code)
        except WorkflowDefinitionError as error:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR) from error
        assignment = await self._repository.get_task_creation_record(approval.current_task_id)
        if assignment is None or assignment.approval_id != approval.id:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        return approval, spec, current_node, assignment

    @staticmethod
    def _decide(status, action, context):
        """调用 P1-002 状态机并统一映射状态与处理人错误。"""
        try:
            return decide_transition(current_status=status, action=action, context=context)
        except ApprovalTransitionError as error:
            ApprovalService._raise_transition_error(error)

    @staticmethod
    def _raise_transition_error(error: ApprovalTransitionError) -> None:
        """将 P1-002 稳定错误码转换为审批应用层错误。"""
        if error.code is ApprovalTransitionErrorCode.INVALID_STATUS_ACTION:
            raise ApprovalError(ApprovalErrorCode.INVALID_TRANSITION) from error
        if error.code is ApprovalTransitionErrorCode.ACTOR_NOT_CURRENT_ASSIGNEE:
            raise ApprovalError(ApprovalErrorCode.FORBIDDEN) from error
        raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR) from error


def _normalize_approve_comment(comment: object) -> str | None:
    """规范化可选 approve comment 并拒绝非字符串或超长内容。"""
    if comment is None:
        return None
    if not isinstance(comment, str):
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    normalized = comment.strip()
    if len(normalized) > 2000:
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    return normalized or None


def _normalize_reject_request(target: object, comment: object) -> tuple[RejectTargetType, str]:
    """验证驳回目标与必填 comment 并返回规范化领域值。"""
    if not isinstance(comment, str):
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    normalized_comment = comment.strip()
    if not 1 <= len(normalized_comment) <= 2000:
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    try:
        reject_target = RejectTargetType(target)
    except (ValueError, TypeError) as error:
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST) from error
    return reject_target, normalized_comment


def _action_response(
    approval: ApprovalApplication,
    action: ApprovalAction,
    current_node,
) -> ApprovalActionResponse:
    """从迁移后的申请构造不暴露内部目标处理人的动作响应。"""
    if (
        approval.workflow_code is None
        or approval.workflow_version is None
        or approval.workflow_instance_id is None
    ):
        raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
    return ApprovalActionResponse(
        approval_id=approval.id,
        action=action,
        status=approval.status,
        workflow_code=approval.workflow_code,
        workflow_version=approval.workflow_version,
        workflow_instance_id=approval.workflow_instance_id,
        current_task_id=approval.current_task_id,
        current_node_code=approval.current_node_code,
        current_node_role=current_node.role if current_node is not None else None,
        completed_at=approval.completed_at,
    )
