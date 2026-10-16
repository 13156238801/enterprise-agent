"""协调审批提交、同意、驳回状态决策及流程运行时历史写入。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.schemas import (
    ApprovalActionResponse,
    ApprovalDetailResponse,
    ApprovalHistoryItem,
    ApprovalHistoryResponse,
    ApprovalListItem,
    ApprovalListResponse,
    MasterDataChangeDetail,
    SubmitApprovalResponse,
)
from app.audit.repository import AuditRepository
from app.auth.context import AuthContext
from app.domain.approval.context import RejectTargetType, TransitionContext
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.approval.errors import ApprovalTransitionError, ApprovalTransitionErrorCode
from app.domain.approval.state_machine import decide_transition
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.domain.workflow.errors import WorkflowDefinitionError
from app.idempotency.service import (
    IdempotencyClaim,
    IdempotencyService,
    canonical_request_hash,
    normalize_idempotency_key,
)
from app.master_data.repository import MasterDataRepository
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange
from app.workflow.repository import WorkflowDefinitionRepository


def validate_expected_version(value: object) -> int:
    """严格校验审批 expected_version，避免 bool 被当作整数接受。"""
    if type(value) is not int or value <= 0:
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    return value


class ApprovalService:
    """协调审批申请写操作及只读查询，并通过 Repository 访问持久化数据。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定请求独享会话并构造不拥有事务边界的 Repository。"""
        self._session = session
        self._repository = ApprovalRepository(session)
        self._workflow_repository = WorkflowDefinitionRepository(session)
        self._master_data_repository = MasterDataRepository(session)
        self._idempotency = IdempotencyService(session)
        self._audit = AuditRepository(session)

    async def _claim_write(
        self,
        actor_id: str,
        key: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_hash: str,
    ) -> IdempotencyClaim:
        """在当前写事务内按 actor、key、action、resource 和指纹 claim。"""
        return await self._idempotency.claim(
            actor_id=actor_id,
            key=key,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_hash=request_hash,
        )

    async def _cas_application(
        self, approval_id: UUID, expected_version: int, values: dict[str, object]
    ) -> int:
        """执行审批申请条件更新；零行命中统一映射版本冲突。"""
        version = await self._repository.update_application_if_version(
            approval_id, expected_version, values
        )
        if version is None:
            raise ApprovalError(ApprovalErrorCode.VERSION_CONFLICT)
        return version

    async def _finish_write(
        self,
        claim: IdempotencyClaim,
        response: BaseModel,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata_json: dict[str, object],
    ) -> None:
        """在同一事务追加白名单审计事实并持久化可重放响应。"""
        await self._audit.append(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata_json,
        )
        await self._idempotency.complete(claim, response)

    async def list_my_approvals(
        self,
        auth_context: AuthContext,
        *,
        status: ApprovalStatus | None,
        limit: int,
        offset: int,
    ) -> ApprovalListResponse:
        """只列出当前申请人自己的审批，并以 limit-plus-one 计算分页状态。"""
        rows = await self._repository.list_by_applicant(
            str(auth_context.user_id), status=status, limit=limit, offset=offset
        )
        has_more = len(rows) > limit
        workflow_cache: dict[tuple[str, int], WorkflowDefinitionSpec | None] = {}
        items = [
            await self._to_list_item(row, workflow_cache=workflow_cache) for row in rows[:limit]
        ]
        return ApprovalListResponse(items=items, limit=limit, offset=offset, has_more=has_more)

    async def list_pending_approvals(
        self, auth_context: AuthContext, *, limit: int, offset: int
    ) -> ApprovalListResponse:
        """校验全部审批中候选的完整性及分配资格后再稳定排序分页。"""
        candidates = await self._repository.list_in_approval_candidates()
        task_ids = [row.current_task_id for row in candidates if row.current_task_id]
        assignments = await self._repository.list_task_creation_records(task_ids)
        eligible: list[tuple[ApprovalApplication, UserRole]] = []
        workflow_cache: dict[tuple[str, int], WorkflowDefinitionSpec | None] = {}
        actor_id = str(auth_context.user_id)

        for approval in candidates:
            self._validate_business_link(approval)
            spec, node = await self._resolve_bound_current_node(
                approval, workflow_cache=workflow_cache
            )
            if (
                spec is None
                or node is None
                or not approval.workflow_instance_id
                or not approval.current_task_id
                or not approval.submitted_at
            ):
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            assignment = assignments.get(approval.current_task_id)
            if assignment is None or assignment.approval_id != approval.id:
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            if node.role not in auth_context.roles:
                continue
            if (
                assignment.target_operator_id is not None
                and assignment.target_operator_id != actor_id
            ):
                continue
            eligible.append((approval, node.role))

        eligible.sort(
            key=lambda item: (
                item[0].submitted_at,
                item[0].created_at,
                item[0].id,
            )
        )
        page = eligible[offset : offset + limit + 1]
        has_more = len(page) > limit
        items = [
            self._make_list_item(approval, change_role=role) for approval, role in page[:limit]
        ]
        return ApprovalListResponse(items=items, limit=limit, offset=offset, has_more=has_more)

    async def get_detail(
        self, approval_id: UUID, auth_context: AuthContext
    ) -> ApprovalDetailResponse:
        """授权后返回申请及其数据库中保存的结构化主数据变更快照。"""
        approval, change, current_node = await self._load_readable_approval(
            approval_id, auth_context
        )
        return ApprovalDetailResponse(
            approval_id=approval.id,
            business_type=approval.business_type,
            business_id=approval.business_id,
            applicant_id=approval.applicant_id,
            status=approval.status,
            workflow_code=approval.workflow_code,
            workflow_version=approval.workflow_version,
            workflow_instance_id=approval.workflow_instance_id,
            current_node_code=approval.current_node_code,
            current_node_role=current_node.role if current_node is not None else None,
            submitted_at=approval.submitted_at,
            completed_at=approval.completed_at,
            created_at=approval.created_at,
            updated_at=approval.updated_at,
            version=approval.version,
            change=MasterDataChangeDetail(
                change_id=change.id,
                entity_type=change.entity_type,
                entity_key=change.entity_key,
                before_data=change.before_data,
                form_data=change.form_data,
                change_items=change.change_items,
                change_reason=change.change_reason,
                base_entity_version=change.base_entity_version,
                created_at=change.created_at,
                updated_at=change.updated_at,
            ),
        )

    async def get_history(
        self, approval_id: UUID, auth_context: AuthContext
    ) -> ApprovalHistoryResponse:
        """复用详情读取权限并按时间顺序返回安全的审批动作快照。"""
        approval, _change, _node = await self._load_readable_approval(approval_id, auth_context)
        records = await self._repository.list_records(approval.id)
        self._validate_history(approval, records)
        return ApprovalHistoryResponse(
            items=[
                ApprovalHistoryItem(
                    record_id=record.id,
                    operator_id=record.operator_id,
                    action=record.action,
                    comment=record.comment,
                    from_status=record.from_status,
                    to_status=record.to_status,
                    from_node_code=record.from_node_code,
                    from_node_name=record.from_node_name,
                    target_node_code=record.target_node_code,
                    target_node_name=record.target_node_name,
                    created_at=record.created_at,
                )
                for record in records
            ]
        )

    async def _load_readable_approval(
        self, approval_id: UUID, auth_context: AuthContext
    ) -> tuple[ApprovalApplication, MasterDataChange, WorkflowNodeDefinition | None]:
        """读取并完整性校验申请，再通过统一策略授权详情与历史访问。"""
        approval = await self._repository.get_application_for_read(approval_id)
        if approval is None:
            raise ApprovalError(ApprovalErrorCode.NOT_FOUND)
        change = self._validate_business_link(approval)
        assignment = None
        if approval.status is ApprovalStatus.IN_APPROVAL:
            _spec, node, assignment = await self._validate_current_task(approval)
        else:
            _spec, node = await self._resolve_bound_current_node(approval)

        actor_id = str(auth_context.user_id)
        if actor_id == approval.applicant_id:
            return approval, change, node
        if approval.status is ApprovalStatus.DRAFT:
            raise ApprovalError(ApprovalErrorCode.FORBIDDEN)

        if (
            approval.status is ApprovalStatus.IN_APPROVAL
            and node is not None
            and assignment is not None
        ):
            is_current_reviewer = node.role in auth_context.roles and (
                assignment.target_operator_id is None or assignment.target_operator_id == actor_id
            )
            if is_current_reviewer:
                return approval, change, node

        reviewer_roles = {UserRole.APPROVER, UserRole.DATA_ADMIN}
        if approval.status in {
            ApprovalStatus.IN_APPROVAL,
            ApprovalStatus.COMPLETED,
        } and auth_context.roles.intersection(reviewer_roles):
            latest_submit = await self._repository.find_latest_submit_record(approval.id)
            if latest_submit is None:
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            if await self._repository.has_reviewer_participated_since(
                approval.id, actor_id, latest_submit
            ):
                return approval, change, node
        raise ApprovalError(ApprovalErrorCode.FORBIDDEN)

    async def _validate_current_task(
        self, approval: ApprovalApplication
    ) -> tuple[WorkflowDefinitionSpec, WorkflowNodeDefinition, ApprovalRecord]:
        """验证审批中申请的精确流程、运行时字段和当前 task 历史关联。"""
        spec, node = await self._resolve_bound_current_node(approval)
        if (
            spec is None
            or node is None
            or not approval.workflow_instance_id
            or not approval.current_task_id
            or not approval.submitted_at
        ):
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        assignment = await self._repository.get_task_creation_record(approval.current_task_id)
        if assignment is None or assignment.approval_id != approval.id:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        return spec, node, assignment

    async def _resolve_bound_current_node(
        self,
        approval: ApprovalApplication,
        *,
        workflow_cache: dict[tuple[str, int], WorkflowDefinitionSpec | None] | None = None,
    ) -> tuple[WorkflowDefinitionSpec | None, WorkflowNodeDefinition | None]:
        """校验流程绑定配对并从精确版本解析可选当前节点。"""
        code, version = approval.workflow_code, approval.workflow_version
        if (code is None) != (version is None):
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        if approval.current_node_code is None:
            return None, None
        if code is None or version is None:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        try:
            key = (code, version)
            if workflow_cache is not None and key in workflow_cache:
                spec = workflow_cache[key]
            else:
                spec = await self._workflow_repository.get_by_code_version(code, version)
                if workflow_cache is not None:
                    workflow_cache[key] = spec
            if spec is None:
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            return spec, spec.get_node(approval.current_node_code)
        except WorkflowDefinitionError as error:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR) from error

    async def _to_list_item(
        self,
        approval: ApprovalApplication,
        *,
        workflow_cache: dict[tuple[str, int], WorkflowDefinitionSpec | None],
    ) -> ApprovalListItem:
        """校验列表行的业务和流程关联后转换为摘要响应。"""
        self._validate_business_link(approval)
        _spec, node = await self._resolve_bound_current_node(
            approval, workflow_cache=workflow_cache
        )
        return self._make_list_item(approval, change_role=node.role if node is not None else None)

    @staticmethod
    def _validate_business_link(approval: ApprovalApplication) -> MasterDataChange:
        """确保申请严格关联唯一的主数据变更对象。"""
        change = approval.master_data_change
        if (
            approval.business_type is not BusinessType.MASTER_DATA_CHANGE
            or change is None
            or approval.business_id != str(change.id)
        ):
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        return change

    @staticmethod
    def _make_list_item(
        approval: ApprovalApplication, *, change_role: UserRole | None
    ) -> ApprovalListItem:
        """将申请及已解析节点角色转换为不暴露 task 的列表摘要。"""
        change = approval.master_data_change
        if change is None:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        return ApprovalListItem(
            approval_id=approval.id,
            business_type=approval.business_type,
            status=approval.status,
            applicant_id=approval.applicant_id,
            entity_type=change.entity_type,
            entity_key=change.entity_key,
            change_reason=change.change_reason,
            workflow_code=approval.workflow_code,
            workflow_version=approval.workflow_version,
            current_node_code=approval.current_node_code,
            current_node_role=change_role,
            submitted_at=approval.submitted_at,
            completed_at=approval.completed_at,
            created_at=approval.created_at,
            updated_at=approval.updated_at,
            version=approval.version,
        )

    @staticmethod
    def _validate_history(approval: ApprovalApplication, records: list[ApprovalRecord]) -> None:
        """拒绝已进入审批但缺少提交或必要动作事实的损坏历史。"""
        if approval.status is ApprovalStatus.DRAFT:
            if not records and approval.workflow_code is not None:
                raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
            return
        actions = {record.action for record in records}
        if ApprovalAction.SUBMIT not in actions:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
        if approval.status is ApprovalStatus.COMPLETED and ApprovalAction.APPROVE not in actions:
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)

    async def submit(
        self,
        approval_id: UUID,
        auth_context: AuthContext,
        expected_version: object = None,
        idempotency_key: object = None,
    ) -> SubmitApprovalResponse:
        """以幂等 claim 和数据库 CAS 原子提交申请并返回新版本。"""
        expected = validate_expected_version(expected_version)
        key = normalize_idempotency_key(idempotency_key)
        actor_id = str(auth_context.user_id)
        request_hash = canonical_request_hash({"expected_version": expected})
        async with self._session.begin():
            claim = await self._claim_write(
                actor_id,
                key,
                "APPROVAL_SUBMIT",
                "approval",
                str(approval_id),
                request_hash,
            )
            if not claim.is_new:
                return self._idempotency.replay(claim, SubmitApprovalResponse)
            approval = await self._repository.get_application_for_action(approval_id)
            if approval is None:
                raise ApprovalError(ApprovalErrorCode.NOT_FOUND)
            change = self._validate_business_link(approval)
            if approval.version != expected:
                raise ApprovalError(ApprovalErrorCode.VERSION_CONFLICT)
            decision = self._decide(
                approval.status,
                ApprovalAction.SUBMIT,
                TransitionContext(actor_id=actor_id, applicant_id=approval.applicant_id),
            )
            entity = await self._master_data_repository.get_entity(
                change.entity_type, change.entity_key
            )
            if entity is None or entity.version != change.base_entity_version:
                raise ApprovalError(ApprovalErrorCode.VERSION_CONFLICT)

            code, workflow_version, spec = await self._resolve_workflow(
                approval, change.entity_type
            )
            first_node = spec.first_node()
            previous_task_id = approval.current_task_id
            previous_node_code = approval.current_node_code
            previous_node_name = approval.current_node_name
            workflow_instance_id = str(uuid4())
            current_task_id = str(uuid4())
            submitted_at = datetime.now(UTC)
            new_version = await self._cas_application(
                approval_id,
                expected,
                {
                    "status": decision.to_status,
                    "workflow_code": code,
                    "workflow_version": workflow_version,
                    "workflow_instance_id": workflow_instance_id,
                    "current_task_id": current_task_id,
                    "current_node_code": first_node.key,
                    "current_node_name": None,
                    "submitted_at": submitted_at,
                    "completed_at": None,
                },
            )
            await self._repository.save_transition(
                ApprovalRecord(
                    approval_id=approval.id,
                    operator_id=actor_id,
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
            response = SubmitApprovalResponse(
                approval_id=approval.id,
                status=decision.to_status,
                workflow_code=code,
                workflow_version=workflow_version,
                workflow_instance_id=workflow_instance_id,
                current_task_id=current_task_id,
                current_node_code=first_node.key,
                current_node_role=first_node.role,
                submitted_at=submitted_at,
                version=new_version,
            )
            await self._finish_write(
                claim,
                response,
                actor_id=actor_id,
                action="APPROVAL_SUBMIT",
                resource_type="approval",
                resource_id=str(approval.id),
                metadata_json={
                    "approval_id": str(approval.id),
                    "from_status": decision.from_status.value,
                    "to_status": decision.to_status.value,
                    "from_version": expected,
                    "to_version": new_version,
                    "workflow_code": code,
                    "workflow_version": workflow_version,
                    "target_node_code": first_node.key,
                },
            )
        return response

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
        self,
        approval_id: UUID,
        auth_context: AuthContext,
        expected_version: object = None,
        comment: object = None,
        idempotency_key: object = None,
    ) -> ApprovalActionResponse:
        """以状态机决策和数据库 CAS 原子通过当前审批节点。"""
        expected = validate_expected_version(expected_version)
        normalized_comment = _normalize_approve_comment(comment)
        key = normalize_idempotency_key(idempotency_key)
        actor_id = str(auth_context.user_id)
        request_hash = canonical_request_hash(
            {"expected_version": expected, "comment": normalized_comment}
        )
        async with self._session.begin():
            claim = await self._claim_write(
                actor_id, key, "APPROVAL_APPROVE", "approval", str(approval_id), request_hash
            )
            if not claim.is_new:
                return self._idempotency.replay(claim, ApprovalActionResponse)
            approval, spec, current_node, assignment = await self._load_action_context(
                approval_id, ApprovalAction.APPROVE, auth_context, expected_version=expected
            )
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
            completed_at = None
            if decision.to_status is ApprovalStatus.COMPLETED:
                completed_at = datetime.now(UTC)
                next_task_id = next_node_code = None
            else:
                target_node = spec.next_node(current_node.key)
                if target_node is None:
                    raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
                target_task_id = str(uuid4())
                next_task_id, next_node_code = target_task_id, target_node.key
            new_version = await self._cas_application(
                approval_id,
                expected,
                {
                    "status": decision.to_status,
                    "current_task_id": next_task_id,
                    "current_node_code": next_node_code,
                    "current_node_name": None,
                    "completed_at": completed_at,
                },
            )
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
            response = _action_response(
                approval,
                decision.action,
                target_node,
                status=decision.to_status,
                current_task_id=target_task_id,
                current_node_code=target_node.key if target_node is not None else None,
                completed_at=completed_at,
                version=new_version,
            )
            await self._finish_write(
                claim,
                response,
                actor_id=actor_id,
                action="APPROVAL_APPROVE",
                resource_type="approval",
                resource_id=str(approval.id),
                metadata_json={
                    "approval_id": str(approval.id),
                    "from_status": decision.from_status.value,
                    "to_status": decision.to_status.value,
                    "from_version": expected,
                    "to_version": new_version,
                    "from_node_code": old_node_code,
                    "target_node_code": target_node.key if target_node is not None else None,
                },
            )
        return response

    async def reject(
        self,
        approval_id: UUID,
        auth_context: AuthContext,
        target: object,
        comment: object,
        expected_version: object = None,
        idempotency_key: object = None,
    ) -> ApprovalActionResponse:
        """解析可信驳回目标后以 CAS 更新运行时、历史、审计和重放结果。"""
        expected = validate_expected_version(expected_version)
        reject_target, normalized_comment = _normalize_reject_request(target, comment)
        key = normalize_idempotency_key(idempotency_key)
        actor_id = str(auth_context.user_id)
        request_hash = canonical_request_hash(
            {
                "expected_version": expected,
                "target": reject_target.value,
                "comment": normalized_comment,
            }
        )
        async with self._session.begin():
            claim = await self._claim_write(
                actor_id, key, "APPROVAL_REJECT", "approval", str(approval_id), request_hash
            )
            if not claim.is_new:
                return self._idempotency.replay(claim, ApprovalActionResponse)
            approval, spec, current_node, assignment = await self._load_action_context(
                approval_id, ApprovalAction.REJECT, auth_context, expected_version=expected
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
            next_node_code = None
            if reject_target is RejectTargetType.APPLICANT:
                next_task_id = None
            else:
                if target_node is None or target_operator_id is None:
                    raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)
                target_task_id = str(uuid4())
                next_task_id = target_task_id
                next_node_code = target_node.key
            new_version = await self._cas_application(
                approval_id,
                expected,
                {
                    "status": decision.to_status,
                    "current_task_id": next_task_id,
                    "current_node_code": next_node_code,
                    "current_node_name": None,
                    "completed_at": None,
                },
            )

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
            response = _action_response(
                approval,
                decision.action,
                target_node,
                status=decision.to_status,
                current_task_id=target_task_id,
                current_node_code=next_node_code,
                completed_at=None,
                version=new_version,
            )
            await self._finish_write(
                claim,
                response,
                actor_id=actor_id,
                action="APPROVAL_REJECT",
                resource_type="approval",
                resource_id=str(approval.id),
                metadata_json={
                    "approval_id": str(approval.id),
                    "from_status": decision.from_status.value,
                    "to_status": decision.to_status.value,
                    "from_version": expected,
                    "to_version": new_version,
                    "from_node_code": old_node_code,
                    "target_node_code": next_node_code,
                },
            )
        return response

    async def withdraw(
        self,
        approval_id: UUID,
        auth_context: AuthContext,
        expected_version: object = None,
        comment: object = None,
        idempotency_key: object = None,
    ) -> ApprovalActionResponse:
        """以状态机决策和 CAS 原子撤回并保存审计及幂等响应。"""
        expected = validate_expected_version(expected_version)
        normalized_comment = _normalize_withdraw_comment(comment)
        key = normalize_idempotency_key(idempotency_key)
        actor_id = str(auth_context.user_id)
        request_hash = canonical_request_hash(
            {"expected_version": expected, "comment": normalized_comment}
        )
        async with self._session.begin():
            claim = await self._claim_write(
                actor_id, key, "APPROVAL_WITHDRAW", "approval", str(approval_id), request_hash
            )
            if not claim.is_new:
                return self._idempotency.replay(claim, ApprovalActionResponse)
            approval, spec, current_node, _assignment = await self._load_action_context(
                approval_id, ApprovalAction.WITHDRAW, auth_context, expected_version=expected
            )
            context = TransitionContext(
                actor_id=actor_id,
                applicant_id=approval.applicant_id,
                is_first_approval_node=spec.is_first_node(current_node.key),
            )
            decision = self._decide(approval.status, ApprovalAction.WITHDRAW, context)
            old_task_id = approval.current_task_id
            old_node_code = approval.current_node_code
            old_node_name = approval.current_node_name
            new_version = await self._cas_application(
                approval_id,
                expected,
                {
                    "status": decision.to_status,
                    "current_task_id": None,
                    "current_node_code": None,
                    "current_node_name": None,
                    "completed_at": None,
                },
            )
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
                    target_task_id=None,
                    target_node_code=None,
                    target_node_name=None,
                    target_operator_id=approval.applicant_id,
                )
            )
            response = _action_response(
                approval,
                decision.action,
                None,
                status=decision.to_status,
                current_task_id=None,
                current_node_code=None,
                completed_at=None,
                version=new_version,
            )
            await self._finish_write(
                claim,
                response,
                actor_id=actor_id,
                action="APPROVAL_WITHDRAW",
                resource_type="approval",
                resource_id=str(approval.id),
                metadata_json={
                    "approval_id": str(approval.id),
                    "from_status": decision.from_status.value,
                    "to_status": decision.to_status.value,
                    "from_version": expected,
                    "to_version": new_version,
                    "from_node_code": old_node_code,
                },
            )
        return response

    async def _load_action_context(
        self,
        approval_id: UUID,
        action: ApprovalAction,
        auth_context: AuthContext,
        *,
        expected_version: int,
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
        # 已知过期版本先返回冲突；并发事务都读到同版时仍只由下方 CAS 决胜。
        if approval.version != expected_version:
            raise ApprovalError(ApprovalErrorCode.VERSION_CONFLICT)
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
                    is_first_approval_node=action is ApprovalAction.WITHDRAW,
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
        if error.code is ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT:
            raise ApprovalError(ApprovalErrorCode.FORBIDDEN) from error
        if error.code is ApprovalTransitionErrorCode.WITHDRAW_NOT_FIRST_NODE:
            raise ApprovalError(ApprovalErrorCode.WITHDRAW_NOT_ALLOWED) from error
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


def _normalize_withdraw_comment(comment: object) -> str | None:
    """清理可选撤回意见，并拒绝非字符串或超过 2000 字符的内容。"""
    if comment is None:
        return None
    if not isinstance(comment, str):
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    normalized = comment.strip()
    if len(normalized) > 2000:
        raise ApprovalError(ApprovalErrorCode.INVALID_REQUEST)
    return normalized or None


def _action_response(
    approval: ApprovalApplication,
    action: ApprovalAction,
    current_node,
    *,
    status: ApprovalStatus,
    current_task_id: str | None,
    current_node_code: str | None,
    completed_at,
    version: int,
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
        status=status,
        workflow_code=approval.workflow_code,
        workflow_version=approval.workflow_version,
        workflow_instance_id=approval.workflow_instance_id,
        current_task_id=current_task_id,
        current_node_code=current_node_code,
        current_node_role=current_node.role if current_node is not None else None,
        completed_at=completed_at,
        version=version,
    )
