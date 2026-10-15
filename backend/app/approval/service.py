"""协调 Submit 状态决策、工作流绑定、运行时初始化和历史写入。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.schemas import SubmitApprovalResponse
from app.auth.context import AuthContext
from app.domain.approval.context import TransitionContext
from app.domain.approval.enums import ApprovalAction, BusinessType
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
