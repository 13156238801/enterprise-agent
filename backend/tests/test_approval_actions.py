"""覆盖审批处理 API 与 PostgreSQL 状态迁移。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api.deps import get_approval_service, get_auth_service
from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.schemas import ApprovalActionResponse
from app.approval.service import ApprovalService
from app.auth.context import AuthContext
from app.auth.errors import AuthError, AuthErrorCode
from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.domain.approval.context import RejectTargetType
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.infrastructure.database import create_db_engine, create_session_factory
from app.main import create_app
from app.master_data.schemas import ChangeDraftRequest
from app.master_data.service import MasterDataService
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange, MasterDataEntity
from app.models.workflow import WorkflowDefinition


def _client_for(auth_context: AuthContext, service: object) -> TestClient:
    """构造注入指定可信身份与服务的测试客户端。"""

    class FakeAuthService:
        """为路由测试返回固定认证上下文。"""

        async def authenticate(self, token: str) -> AuthContext:
            """校验测试 Bearer token 并返回可信身份。"""
            assert token == "test-token"
            return auth_context

    settings = Settings(_env_file=None)
    api = create_app(settings)
    api.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    api.dependency_overrides[get_approval_service] = lambda: service
    return TestClient(RequestIdMiddleware(api, settings))


def _auth(roles: frozenset[UserRole], user_id: UUID | None = None) -> AuthContext:
    """构造仅用于测试的可信用户上下文。"""
    return AuthContext(
        session_id=uuid4(),
        user_id=user_id or uuid4(),
        username="reviewer",
        display_name="Reviewer",
        roles=roles,
    )


def test_approve_and_reject_require_bearer_authentication() -> None:
    """验证 approve 和 reject 未认证时返回 Bearer 401。"""
    settings = Settings(_env_file=None)
    api = create_app(settings)

    class UnauthenticatedService:
        """模拟认证依赖对缺失凭据的拒绝。"""

        async def authenticate(self, token: str) -> AuthContext:
            """对缺失 Bearer 凭据返回稳定未认证错误。"""
            raise AuthError(AuthErrorCode.UNAUTHENTICATED)

    api.dependency_overrides[get_auth_service] = lambda: UnauthenticatedService()
    client = TestClient(RequestIdMiddleware(api, settings))
    approval_id = uuid4()
    for action, body in (("approve", {}), ("reject", {"target": "APPLICANT", "comment": "退回"})):
        response = client.post(f"/api/v1/approvals/{approval_id}/{action}", json=body)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"


def test_approve_and_reject_route_require_explicit_reviewer_role() -> None:
    """验证 APPLICANT 与 ADMIN 不通过路由审批角色门禁且不调用服务。"""

    class NeverCalledService:
        """一旦路由错误调用服务便使测试失败。"""

        async def approve(self, *args, **kwargs):
            """拒绝不应发生的 approve 服务调用。"""
            raise AssertionError("service must not be called")

        async def reject(self, *args, **kwargs):
            """拒绝不应发生的 reject 服务调用。"""
            raise AssertionError("service must not be called")

    for role in (UserRole.APPLICANT, UserRole.ADMIN):
        client = _client_for(_auth(frozenset({role})), NeverCalledService())
        approval_id = uuid4()
        for action, body in (
            ("approve", {}),
            ("reject", {"target": "APPLICANT", "comment": "退回"}),
        ):
            response = client.post(
                f"/api/v1/approvals/{approval_id}/{action}",
                headers={"Authorization": "Bearer test-token", "X-Request-ID": "role-test"},
                json=body,
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"
            assert response.json()["error"]["request_id"] == "role-test"


@pytest.mark.parametrize("role", [UserRole.APPROVER, UserRole.DATA_ADMIN])
def test_approve_and_reject_routes_allow_coarse_reviewer_roles(role: UserRole) -> None:
    """验证 APPROVER 与 DATA_ADMIN 均可通过路由门禁进入对应 Service。"""
    approval_id = uuid4()
    auth_context = _auth(frozenset({role}))

    class RecordingService:
        """记录动作调用并返回安全的流程状态响应。"""

        calls: list[tuple[str, object]] = []

        async def approve(self, received_id, received_auth, comment=None):
            """记录 approve 调用并构造测试响应。"""
            assert received_id == approval_id
            assert received_auth is auth_context
            self.calls.append(("approve", comment))
            return ApprovalActionResponse(
                approval_id=approval_id,
                action=ApprovalAction.APPROVE,
                status=ApprovalStatus.IN_APPROVAL,
                workflow_code="supplier_change",
                workflow_version=1,
                workflow_instance_id=str(uuid4()),
                current_task_id=str(uuid4()),
                current_node_code="review",
                current_node_role=role,
                completed_at=None,
            )

        async def reject(self, received_id, received_auth, target, comment):
            """记录 reject 调用并构造测试响应。"""
            assert received_id == approval_id
            assert received_auth is auth_context
            self.calls.append(("reject", (target, comment)))
            return ApprovalActionResponse(
                approval_id=approval_id,
                action=ApprovalAction.REJECT,
                status=ApprovalStatus.DRAFT,
                workflow_code="supplier_change",
                workflow_version=1,
                workflow_instance_id=str(uuid4()),
                current_task_id=None,
                current_node_code=None,
                current_node_role=None,
                completed_at=None,
            )

    service = RecordingService()
    client = _client_for(auth_context, service)
    headers = {"Authorization": "Bearer test-token"}
    approve = client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=headers, json={"comment": "ok"}
    )
    reject = client.post(
        f"/api/v1/approvals/{approval_id}/reject",
        headers=headers,
        json={"target": "APPLICANT", "comment": "revise"},
    )
    assert approve.status_code == reject.status_code == 200
    assert [call[0] for call in service.calls] == ["approve", "reject"]


def test_approve_and_reject_invalid_comments_use_approval_error_envelope() -> None:
    """验证 Service 的无效业务输入进入审批 ErrorEnvelope 并保留 request ID。"""
    client = _client_for(
        _auth(frozenset({UserRole.APPROVER})),
        ApprovalService(object()),  # 验证应在开启事务或访问 Session 前失败。
    )
    approval_id = uuid4()
    headers = {"Authorization": "Bearer test-token", "X-Request-ID": "invalid-action"}
    responses = [
        client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=headers,
            json={"comment": "x" * 2001},
        ),
        client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=headers,
            json={"comment": 42},
        ),
        client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            headers=headers,
            json={"target": "INVALID", "comment": "退回"},
        ),
        client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            headers=headers,
            json={"target": "APPLICANT", "comment": " "},
        ),
        client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            headers=headers,
            json={"target": "APPLICANT", "comment": 42},
        ),
        client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            headers=headers,
            json={"target": "APPLICANT", "comment": "x" * 2001},
        ),
    ]
    for response in responses:
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "APPROVAL_INVALID_REQUEST"
        assert response.json()["error"]["request_id"] == "invalid-action"


def test_missing_runtime_defers_status_legality_to_state_machine() -> None:
    """验证缺少运行时字段时仍由状态机优先判定动作，并保留审批中完整性错误。"""

    async def run() -> None:
        """使用轻量申请对象验证状态机预判不依赖数据库。"""
        for status in (ApprovalStatus.DRAFT, ApprovalStatus.COMPLETED):
            for action in (ApprovalAction.APPROVE, ApprovalAction.REJECT):
                approval_id = uuid4()
                change_id = uuid4()
                application = SimpleNamespace(
                    id=approval_id,
                    business_type=BusinessType.MASTER_DATA_CHANGE,
                    business_id=str(change_id),
                    master_data_change=SimpleNamespace(id=change_id),
                    applicant_id=str(uuid4()),
                    status=status,
                    workflow_code=None,
                    workflow_version=None,
                    workflow_instance_id=None,
                    current_task_id=None,
                    current_node_code=None,
                )
                service = ApprovalService(object())

                async def get_application_for_action(received_id: UUID):
                    """返回指定的轻量申请，避免测试连接数据库。"""
                    assert received_id == approval_id
                    return application

                service._repository.get_application_for_action = get_application_for_action
                auth = _auth(frozenset({UserRole.APPROVER}))
                with pytest.raises(ApprovalError) as error:
                    await service._load_action_context(approval_id, action, auth)
                assert error.value.code == ApprovalErrorCode.INVALID_TRANSITION.value

        approval_id = uuid4()
        change_id = uuid4()
        application = SimpleNamespace(
            id=approval_id,
            business_type=BusinessType.MASTER_DATA_CHANGE,
            business_id=str(change_id),
            master_data_change=SimpleNamespace(id=change_id),
            applicant_id=str(uuid4()),
            status=ApprovalStatus.IN_APPROVAL,
            workflow_code=None,
            workflow_version=None,
            workflow_instance_id=None,
            current_task_id=None,
            current_node_code=None,
        )
        service = ApprovalService(object())

        async def get_corrupt_application(received_id: UUID):
            """返回缺少流程运行时字段的审批中申请。"""
            assert received_id == approval_id
            return application

        service._repository.get_application_for_action = get_corrupt_application
        with pytest.raises(ApprovalError) as error:
            await service._load_action_context(
                approval_id,
                ApprovalAction.APPROVE,
                _auth(frozenset({UserRole.APPROVER})),
            )
        assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value

    asyncio.run(run())


def test_reject_structural_extra_fields_keep_fastapi_validation_behavior() -> None:
    """验证客户端注入目标节点字段仍由 extra-forbid 结构校验拒绝。"""
    client = _client_for(_auth(frozenset({UserRole.APPROVER})), object())
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/reject",
        headers={"Authorization": "Bearer test-token"},
        json={
            "target": "PREVIOUS_NODE",
            "comment": "reject",
            "target_node_code": "attacker_node",
            "target_operator_id": "attacker",
        },
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_postgres_approval_actions_runtime_history_permissions_and_rollback(monkeypatch) -> None:
    """在真实 PostgreSQL 验证审批迁移、授权、精确流程和事务回滚。"""
    from app.core.config import get_settings
    from app.domain.workflow.definition import workflow_definition_to_dict

    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    workflow_code = f"actions_{uuid4().hex}"
    previous_operator = str(uuid4())
    seeded_ids: list = []
    seeded_change_ids: list[UUID] = []
    seeded_entity_keys: list[str] = []
    v1 = WorkflowDefinitionSpec(
        code=workflow_code,
        version=1,
        nodes=(
            WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
            WorkflowNodeDefinition("data_admin_review", UserRole.DATA_ADMIN),
        ),
    )
    v2 = WorkflowDefinitionSpec(
        code=workflow_code,
        version=2,
        nodes=(
            WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
            WorkflowNodeDefinition("final_review", UserRole.APPROVER),
        ),
    )

    async def add_case(
        session,
        *,
        node: str,
        status: ApprovalStatus = ApprovalStatus.IN_APPROVAL,
        task_operator: str | None = None,
        include_task_history: bool = True,
        include_previous_approval: bool = False,
    ) -> tuple[UUID, UUID, str, datetime]:
        """写入一组绑定 v1 的审批、变更和可选任务创建历史。"""
        approval_id, change_id = uuid4(), uuid4()
        task_id = str(uuid4())
        submitted_at = datetime.now(UTC)
        application = ApprovalApplication(
            id=approval_id,
            business_type=BusinessType.MASTER_DATA_CHANGE,
            business_id=str(change_id),
            applicant_id=str(uuid4()),
            status=status,
            workflow_code=workflow_code,
            workflow_version=1,
            workflow_instance_id=str(uuid4()),
            current_task_id=task_id,
            current_node_code=node,
            current_node_name="legacy display name",
            submitted_at=submitted_at,
        )
        change = MasterDataChange(
            id=change_id,
            approval_id=approval_id,
            entity_type="supplier",
            entity_key=f"action-{uuid4().hex}",
            before_data={"name": "before"},
            base_entity_version=1,
            form_data={"name": "after"},
            change_items=[{"field": "name", "operation": "UPDATE"}],
        )
        entity = MasterDataEntity(
            entity_type="supplier",
            entity_key=change.entity_key,
            data_json={"name": "current"},
            version=3,
        )
        session.add_all([application, change, entity])
        seeded_change_ids.append(change_id)
        seeded_entity_keys.append(change.entity_key)
        if include_task_history and not include_previous_approval:
            session.add(
                ApprovalRecord(
                    approval_id=approval_id,
                    operator_id=str(uuid4()),
                    action=ApprovalAction.SUBMIT,
                    from_status=ApprovalStatus.DRAFT,
                    to_status=ApprovalStatus.IN_APPROVAL,
                    target_task_id=task_id,
                    target_node_code=node,
                    target_operator_id=task_operator,
                )
            )
        if include_previous_approval:
            session.add(
                ApprovalRecord(
                    approval_id=approval_id,
                    operator_id=previous_operator,
                    action=ApprovalAction.APPROVE,
                    from_status=ApprovalStatus.IN_APPROVAL,
                    to_status=ApprovalStatus.IN_APPROVAL,
                    from_task_id=str(uuid4()),
                    from_node_code="manager_review",
                    target_task_id=task_id,
                    target_node_code="data_admin_review",
                    target_operator_id=None,
                )
            )
        await session.flush()
        seeded_ids.append(approval_id)
        return approval_id, change_id, task_id, submitted_at

    async def run() -> None:
        """逐项执行 PostgreSQL 状态迁移断言并清理随机测试数据。"""
        try:
            async with factory() as session:
                session.add_all(
                    [
                        WorkflowDefinition(
                            code=workflow_code,
                            version=1,
                            definition_json=workflow_definition_to_dict(v1),
                            active=False,
                        ),
                        WorkflowDefinition(
                            code=workflow_code,
                            version=2,
                            definition_json=workflow_definition_to_dict(v2),
                            active=True,
                        ),
                    ]
                )
                await session.flush()
                manager_id, _, old_manager_task, submitted_at = await add_case(
                    session, node="manager_review"
                )
                final_id, _, _, _ = await add_case(session, node="data_admin_review")
                reject_applicant_id, _, _, _ = await add_case(session, node="manager_review")
                (
                    reject_resubmit_id,
                    reject_resubmit_change_id,
                    reject_resubmit_task,
                    _,
                ) = await add_case(session, node="manager_review")
                reject_previous_id, _, old_return_task, _ = await add_case(
                    session,
                    node="data_admin_review",
                    include_previous_approval=True,
                )
                targeted_id, _, _, _ = await add_case(
                    session,
                    node="manager_review",
                    task_operator=previous_operator,
                )
                wrong_role_id, _, _, _ = await add_case(
                    session,
                    node="manager_review",
                    task_operator=previous_operator,
                )
                no_history_id, _, _, _ = await add_case(
                    session,
                    node="manager_review",
                    include_task_history=False,
                )
                no_previous_history_id, _, _, _ = await add_case(
                    session,
                    node="data_admin_review",
                )
                missing_change_id, missing_change_pk, _, _ = await add_case(
                    session, node="manager_review"
                )
                mismatch_id, _, _, _ = await add_case(session, node="manager_review")
                missing_exact_id, _, _, _ = await add_case(session, node="manager_review")
                corrupt_definition_id, _, _, _ = await add_case(session, node="manager_review")
                missing_runtime_id, _, _, _ = await add_case(session, node="manager_review")
                unknown_node_id, _, _, _ = await add_case(session, node="manager_review")
                session.add(
                    WorkflowDefinition(
                        code=workflow_code,
                        version=3,
                        definition_json={"corrupt": True},
                        active=False,
                    )
                )
                await session.flush()
                mismatch = await session.get(ApprovalApplication, mismatch_id)
                corrupt = await session.get(ApprovalApplication, corrupt_definition_id)
                missing_runtime = await session.get(ApprovalApplication, missing_runtime_id)
                unknown_node = await session.get(ApprovalApplication, unknown_node_id)
                assert mismatch is not None and corrupt is not None
                assert missing_runtime is not None and unknown_node is not None
                mismatch.business_id = str(uuid4())
                corrupt.workflow_version = 3
                missing_runtime.current_node_code = None
                unknown_node.current_node_code = "unknown_node"
                rollback_approve_id, _, _, _ = await add_case(session, node="manager_review")
                rollback_reject_id, _, _, _ = await add_case(
                    session,
                    node="data_admin_review",
                    include_previous_approval=True,
                )
                # 首节点用独立申请，确保 previous-node 错误来自流程定义边界。
                first_node_reject_id, _, _, _ = await add_case(session, node="manager_review")
                # 首节点拒绝前序需要完整任务来源，但不应存在前序批准历史。
                await session.commit()
                await session.execute(
                    delete(MasterDataChange).where(MasterDataChange.id == missing_change_pk)
                )
                await session.commit()

            manager_auth = _auth(frozenset({UserRole.APPROVER}))
            data_admin_auth = _auth(frozenset({UserRole.DATA_ADMIN}))
            async with factory() as session:
                result = await ApprovalService(session).approve(
                    manager_id, manager_auth, "  同意  "
                )
                assert result.status is ApprovalStatus.IN_APPROVAL
                assert result.current_node_code == "data_admin_review"
                assert result.current_task_id != old_manager_task
                assert result.current_node_role is UserRole.DATA_ADMIN
                async with factory() as verify:
                    application = await verify.get(ApprovalApplication, manager_id)
                    assert application is not None
                    assert application.workflow_version == 1
                    assert application.submitted_at == submitted_at
                    assert application.version == 1
                    record = await verify.scalar(
                        select(ApprovalRecord).where(
                            ApprovalRecord.approval_id == manager_id,
                            ApprovalRecord.action == ApprovalAction.APPROVE,
                        )
                    )
                    assert record is not None
                    assert record.comment == "同意"
                    assert record.from_task_id == old_manager_task
                    assert record.target_operator_id is None

            async with factory() as session:
                final_before = await session.get(ApprovalApplication, final_id)
                assert final_before is not None
                final_workflow_instance = final_before.workflow_instance_id
                final_submitted_at = final_before.submitted_at
                final_version = final_before.version
                final_task_id = final_before.current_task_id
                final_node_code = final_before.current_node_code
            async with factory() as session:
                result = await ApprovalService(session).approve(final_id, data_admin_auth)
                assert result.status is ApprovalStatus.COMPLETED
                assert result.current_task_id is None and result.current_node_code is None
                assert result.current_node_role is None
                assert result.completed_at is not None and result.completed_at.tzinfo is not None
            async with factory() as verify:
                completed = await verify.get(ApprovalApplication, final_id)
                assert completed is not None
                assert completed.status is ApprovalStatus.COMPLETED
                assert completed.workflow_code == workflow_code
                assert completed.workflow_version == 1
                assert completed.workflow_instance_id == final_workflow_instance
                assert completed.submitted_at == final_submitted_at
                assert completed.version == final_version
                assert completed.current_task_id is None
                assert completed.current_node_code is None
                assert completed.current_node_name is None
                assert (
                    completed.completed_at is not None and completed.completed_at.tzinfo is not None
                )
                final_record = await verify.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.approval_id == final_id,
                        ApprovalRecord.action == ApprovalAction.APPROVE,
                    )
                )
                assert final_record is not None
                assert final_record.from_task_id == final_task_id
                assert final_record.from_node_code == final_node_code
                assert final_record.target_task_id is None
                assert final_record.target_node_code is None
                assert final_record.target_node_name is None
                assert final_record.target_operator_id is None
            async with factory() as session:
                result = await ApprovalService(session).reject(
                    reject_applicant_id, manager_auth, RejectTargetType.APPLICANT, "  请修改  "
                )
                assert result.status is ApprovalStatus.DRAFT
                assert result.workflow_instance_id
                assert result.current_task_id is None and result.current_node_code is None
                record = await session.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.approval_id == reject_applicant_id,
                        ApprovalRecord.action == ApprovalAction.REJECT,
                    )
                )
                application = await session.get(ApprovalApplication, reject_applicant_id)
                assert record is not None and record.target_operator_id == application.applicant_id
                assert record.target_task_id is None and record.target_node_code is None
                assert record.comment == "请修改"

            # 真实驳回产生无运行时字段的 DRAFT；P1-002 必须先判定状态非法。
            async with factory() as session:
                result = await ApprovalService(session).reject(
                    reject_resubmit_id, manager_auth, RejectTargetType.APPLICANT, "请修改后重提"
                )
                assert result.status is ApprovalStatus.DRAFT
                assert result.current_task_id is None and result.current_node_code is None
            for action in ("approve", "reject"):
                async with factory() as session:
                    with pytest.raises(ApprovalError) as error:
                        service = ApprovalService(session)
                        if action == "approve":
                            await service.approve(reject_resubmit_id, data_admin_auth)
                        else:
                            await service.reject(
                                reject_resubmit_id,
                                data_admin_auth,
                                RejectTargetType.APPLICANT,
                                "非法再次驳回",
                            )
                    assert error.value.code == ApprovalErrorCode.INVALID_TRANSITION.value

            async with factory() as session:
                draft = await session.get(ApprovalApplication, reject_resubmit_id)
                change = await session.get(MasterDataChange, reject_resubmit_change_id)
                assert draft is not None and change is not None
                original_instance_id = draft.workflow_instance_id
                original_submitted_at = draft.submitted_at
                original_applicant_id = UUID(draft.applicant_id)
                original_entity_key = change.entity_key
                original_workflow = (draft.workflow_code, draft.workflow_version)
            async with factory() as session:
                updated = await MasterDataService(session).create_or_update_draft(
                    "supplier",
                    original_entity_key,
                    ChangeDraftRequest(
                        draft_approval_id=reject_resubmit_id,
                        changes={"name": "edited after"},
                        reason="更新后重新提交",
                    ),
                    _auth(frozenset({UserRole.APPLICANT}), original_applicant_id),
                )
                assert updated.approval_id == reject_resubmit_id
            async with factory() as session:
                resubmitted = await ApprovalService(session).submit(
                    reject_resubmit_id,
                    _auth(frozenset({UserRole.APPLICANT}), original_applicant_id),
                )
                assert resubmitted.status is ApprovalStatus.IN_APPROVAL
                assert resubmitted.current_node_code == "manager_review"
                assert resubmitted.current_task_id != reject_resubmit_task
                assert resubmitted.workflow_code == workflow_code
                assert resubmitted.workflow_version == 1
                assert resubmitted.workflow_instance_id != original_instance_id
                assert resubmitted.submitted_at > original_submitted_at
                assert original_workflow == (workflow_code, 1)
            async with factory() as verify:
                resubmitted_application = await verify.get(ApprovalApplication, reject_resubmit_id)
                assert resubmitted_application is not None
                assert (
                    resubmitted_application.workflow_code,
                    resubmitted_application.workflow_version,
                ) == original_workflow
                assert resubmitted_application.workflow_instance_id != original_instance_id
                assert resubmitted_application.current_task_id == resubmitted.current_task_id
                assert resubmitted_application.current_node_code == "manager_review"
                assert resubmitted_application.submitted_at == resubmitted.submitted_at

            for action in ("approve", "reject"):
                async with factory() as session:
                    with pytest.raises(ApprovalError) as error:
                        service = ApprovalService(session)
                        if action == "approve":
                            await service.approve(final_id, data_admin_auth)
                        else:
                            await service.reject(
                                final_id,
                                data_admin_auth,
                                RejectTargetType.APPLICANT,
                                "已完成申请不可驳回",
                            )
                    assert error.value.code == ApprovalErrorCode.INVALID_TRANSITION.value

            async with factory() as session:
                result = await ApprovalService(session).reject(
                    reject_previous_id, data_admin_auth, "PREVIOUS_NODE", "退回上一节点"
                )
                assert result.status is ApprovalStatus.IN_APPROVAL
                assert result.current_node_code == "manager_review"
                assert result.current_task_id != old_return_task
                assert result.workflow_version == 1
                targeted_record = await session.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.approval_id == reject_previous_id,
                        ApprovalRecord.action == ApprovalAction.REJECT,
                    )
                )
                assert targeted_record is not None
                assert targeted_record.target_operator_id == previous_operator
                assert targeted_record.target_node_name is None
                assert targeted_record.target_task_id == result.current_task_id
                assert targeted_record.comment == "退回上一节点"
            async with factory() as session:
                returned = await ApprovalService(session).approve(
                    reject_previous_id,
                    _auth(frozenset({UserRole.APPROVER}), UUID(previous_operator)),
                )
                assert returned.current_node_code == "data_admin_review"

            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).approve(
                        targeted_id, _auth(frozenset({UserRole.APPROVER}))
                    )
                assert error.value.code == ApprovalErrorCode.FORBIDDEN.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).approve(
                        wrong_role_id,
                        _auth(frozenset({UserRole.DATA_ADMIN}), UUID(previous_operator)),
                    )
                assert error.value.code == ApprovalErrorCode.FORBIDDEN.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).approve(no_history_id, manager_auth)
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).reject(
                        no_previous_history_id,
                        data_admin_auth,
                        "PREVIOUS_NODE",
                        "history missing",
                    )
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).reject(
                        first_node_reject_id, manager_auth, "PREVIOUS_NODE", "不应成功"
                    )
                assert error.value.code == ApprovalErrorCode.REJECT_TARGET_UNAVAILABLE.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).approve(uuid4(), manager_auth)
                assert error.value.code == ApprovalErrorCode.NOT_FOUND.value
            for invalid_id in (
                missing_change_id,
                mismatch_id,
                corrupt_definition_id,
                missing_runtime_id,
                unknown_node_id,
            ):
                async with factory() as session:
                    with pytest.raises(ApprovalError) as error:
                        await ApprovalService(session).approve(invalid_id, manager_auth)
                    assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
            async with factory() as session:
                service = ApprovalService(session)

                async def missing_exact_workflow(code, version):
                    """模拟精确版本查询无结果，验证不回退 active workflow。"""
                    return None

                monkeypatch.setattr(
                    service._workflow_repository,
                    "get_by_code_version",
                    missing_exact_workflow,
                )
                with pytest.raises(ApprovalError) as error:
                    await service.approve(missing_exact_id, manager_auth)
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value

            original_save = ApprovalRepository.save_transition

            async def flush_then_fail(repository, record) -> None:
                """先 flush 状态与记录，再注入异常验证整个事务回滚。"""
                await original_save(repository, record)
                raise RuntimeError("injected action failure")

            monkeypatch.setattr(ApprovalRepository, "save_transition", flush_then_fail)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="injected action failure"):
                    await ApprovalService(session).approve(rollback_approve_id, manager_auth)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="injected action failure"):
                    await ApprovalService(session).reject(
                        rollback_reject_id, data_admin_auth, "PREVIOUS_NODE", "rollback"
                    )
            monkeypatch.setattr(ApprovalRepository, "save_transition", original_save)
            async with factory() as session:
                for approval_id in (rollback_approve_id, rollback_reject_id):
                    application = await session.get(ApprovalApplication, approval_id)
                    assert application is not None
                    assert application.status is ApprovalStatus.IN_APPROVAL
                    assert application.current_node_code in {"manager_review", "data_admin_review"}
                    count = await session.scalar(
                        select(func.count())
                        .select_from(ApprovalRecord)
                        .where(
                            ApprovalRecord.approval_id == approval_id,
                            ApprovalRecord.action
                            == (
                                ApprovalAction.APPROVE
                                if approval_id == rollback_approve_id
                                else ApprovalAction.REJECT
                            ),
                        )
                    )
                    assert count == 0
                changes = list(
                    (
                        await session.scalars(
                            select(MasterDataChange).where(
                                MasterDataChange.id.in_(seeded_change_ids)
                            )
                        )
                    ).all()
                )
                entities = list(
                    (
                        await session.scalars(
                            select(MasterDataEntity).where(
                                MasterDataEntity.entity_key.in_(seeded_entity_keys)
                            )
                        )
                    ).all()
                )
                assert all(
                    change.form_data
                    == (
                        {"name": "edited after"}
                        if change.approval_id == reject_resubmit_id
                        else {"name": "after"}
                    )
                    for change in changes
                )
                assert all(entity.data_json == {"name": "current"} for entity in entities)
                assert all(entity.version == 3 for entity in entities)
        finally:
            async with factory() as session:
                if seeded_ids:
                    await session.execute(
                        delete(ApprovalRecord).where(ApprovalRecord.approval_id.in_(seeded_ids))
                    )
                    await session.execute(
                        delete(MasterDataChange).where(MasterDataChange.approval_id.in_(seeded_ids))
                    )
                    await session.execute(
                        delete(MasterDataEntity).where(
                            MasterDataEntity.entity_key.in_(seeded_entity_keys)
                        )
                    )
                    await session.execute(
                        delete(ApprovalApplication).where(ApprovalApplication.id.in_(seeded_ids))
                    )
                await session.execute(
                    delete(WorkflowDefinition).where(WorkflowDefinition.code == workflow_code)
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
