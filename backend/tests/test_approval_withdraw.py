"""覆盖审批申请撤回的 API、校验和 PostgreSQL 生命周期。"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from app.api.deps import get_approval_service, get_auth_service
from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.schemas import ApprovalActionResponse
from app.approval.service import ApprovalService
from app.auth.context import AuthContext
from app.auth.errors import AuthError, AuthErrorCode
from app.core.config import Settings
from app.core.errors import AppError
from app.core.request_context import RequestIdMiddleware
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


def _auth_context(roles: frozenset[UserRole]) -> AuthContext:
    """构造路由测试使用的可信认证上下文。"""
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        username="withdraw-user",
        display_name="Withdraw User",
        roles=roles,
    )


def _client_for(auth: AuthContext, service: object) -> TestClient:
    """创建注入认证身份与撤回服务的 FastAPI 测试客户端。"""

    class FakeAuthService:
        """为路由测试提供固定可信身份。"""

        async def authenticate(self, token: str) -> AuthContext:
            """校验测试 Bearer token 并返回注入身份。"""
            assert token == "test-token"
            return auth

    settings = Settings(_env_file=None)
    api = create_app(settings)
    api.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    api.dependency_overrides[get_approval_service] = lambda: service
    return TestClient(RequestIdMiddleware(api, settings))


def _withdraw_response(approval_id: UUID) -> ApprovalActionResponse:
    """构造 API 测试使用的安全撤回响应。"""
    return ApprovalActionResponse(
        approval_id=approval_id,
        action=ApprovalAction.WITHDRAW,
        status=ApprovalStatus.DRAFT,
        workflow_code="supplier_change",
        workflow_version=1,
        workflow_instance_id=str(uuid4()),
        current_task_id=None,
        current_node_code=None,
        current_node_role=None,
        completed_at=None,
    )


def test_withdraw_api_requires_bearer_and_returns_request_id() -> None:
    """验证匿名撤回返回 Bearer 401 和包含 request_id 的错误 Envelope。"""
    settings = Settings(_env_file=None)
    api = create_app(settings)

    class UnauthenticatedService:
        """模拟撤回端点认证依赖。"""

        async def authenticate(self, token: str) -> AuthContext:
            """对缺失或无效凭据返回未认证错误。"""
            raise AuthError(AuthErrorCode.UNAUTHENTICATED)

    api.dependency_overrides[get_auth_service] = lambda: UnauthenticatedService()
    client = TestClient(RequestIdMiddleware(api, settings))
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/withdraw",
        headers={"X-Request-ID": "withdraw-auth"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
    assert response.json()["error"]["request_id"] == "withdraw-auth"


@pytest.mark.parametrize(
    ("role", "expected_status", "expected_code", "expected_calls"),
    [
        (UserRole.APPLICANT, 200, None, 1),
        (UserRole.APPROVER, 403, "AUTH_PERMISSION_DENIED", 0),
        (UserRole.DATA_ADMIN, 403, "AUTH_PERMISSION_DENIED", 0),
        (UserRole.ADMIN, 403, "AUTH_PERMISSION_DENIED", 0),
    ],
)
def test_withdraw_api_uses_applicant_coarse_role_gate(
    role: UserRole,
    expected_status: int,
    expected_code: str | None,
    expected_calls: int,
) -> None:
    """验证只有 APPLICANT 可进入撤回服务且 ADMIN 不具备隐式权限。"""
    auth = _auth_context(frozenset({role}))
    approval_id = uuid4()

    class RecordingService:
        """记录是否通过 coarse role gate 调用撤回服务。"""

        def __init__(self) -> None:
            """初始化服务调用记录。"""
            self.calls = 0

        async def withdraw(self, received_id, received_auth, comment=None):
            """验证路由向 Service 传入路径 ID、可信身份和可选 comment。"""
            assert received_id == approval_id
            assert received_auth is auth
            self.calls += 1
            return _withdraw_response(approval_id)

    service = RecordingService()
    client = _client_for(auth, service)
    response = client.post(
        f"/api/v1/approvals/{approval_id}/withdraw",
        headers={"Authorization": "Bearer test-token"},
        json={"comment": "撤回说明"},
    )
    assert response.status_code == expected_status
    if expected_code:
        assert response.json()["error"]["code"] == expected_code
    else:
        assert response.json()["action"] == "WITHDRAW"
        assert response.json()["status"] == "DRAFT"
    assert service.calls == expected_calls


@pytest.mark.parametrize("comment", [42, "x" * 2001])
def test_withdraw_service_rejects_invalid_comment_before_database_access(comment: object) -> None:
    """验证非字符串或超长撤回意见在访问数据库前映射为请求错误。"""
    service = ApprovalService(object())
    with pytest.raises(ApprovalError) as error:
        asyncio.run(
            service.withdraw(uuid4(), _auth_context(frozenset({UserRole.APPLICANT})), comment)
        )
    assert error.value.code == ApprovalErrorCode.INVALID_REQUEST.value


def test_withdraw_error_code_maps_to_conflict_envelope() -> None:
    """验证首节点业务限制映射为 HTTP 409 的统一错误响应。"""
    auth = _auth_context(frozenset({UserRole.APPLICANT}))

    class FailingService:
        """模拟应用层返回稳定撤回限制错误。"""

        async def withdraw(self, approval_id, auth_context, comment=None):
            """返回应用错误以验证 HTTP 边界状态码映射。"""
            raise AppError("APPROVAL_WITHDRAW_NOT_ALLOWED", "当前节点不允许撤回")

    client = _client_for(auth, FailingService())
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/withdraw",
        headers={"Authorization": "Bearer test-token", "X-Request-ID": "withdraw-later"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "APPROVAL_WITHDRAW_NOT_ALLOWED"
    assert response.json()["error"]["request_id"] == "withdraw-later"


def test_withdraw_api_accepts_no_body() -> None:
    """验证撤回请求体可省略且 Service 收到空 comment。"""
    approval_id = uuid4()
    auth = _auth_context(frozenset({UserRole.APPLICANT}))

    class RecordingService:
        """记录可选请求体归一化前的服务参数。"""

        async def withdraw(self, received_id, received_auth, comment=None):
            """确认缺省 body 映射成 None comment 并返回成功视图。"""
            assert received_id == approval_id
            assert received_auth is auth
            assert comment is None
            return _withdraw_response(approval_id)

    response = _client_for(auth, RecordingService()).post(
        f"/api/v1/approvals/{approval_id}/withdraw",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "WITHDRAW"
    assert response.json()["status"] == "DRAFT"


def test_withdraw_structural_extra_fields_keep_fastapi_validation_behavior() -> None:
    """验证撤回请求不接受客户端注入状态或申请人字段。"""
    client = _client_for(_auth_context(frozenset({UserRole.APPLICANT})), object())
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/withdraw",
        headers={"Authorization": "Bearer test-token"},
        json={"comment": "withdraw", "applicant_id": "attacker"},
    )
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("comment", [42, "x" * 2001])
def test_withdraw_api_maps_invalid_business_comment_to_error_envelope(comment: object) -> None:
    """验证非字符串或超长 comment 返回带 request_id 的审批 ErrorEnvelope。"""
    client = _client_for(
        _auth_context(frozenset({UserRole.APPLICANT})),
        ApprovalService(object()),
    )
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/withdraw",
        headers={"Authorization": "Bearer test-token", "X-Request-ID": "withdraw-invalid"},
        json={"comment": comment},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "APPROVAL_INVALID_REQUEST"
    assert response.json()["error"]["request_id"] == "withdraw-invalid"


def test_postgres_approval_withdraw_lifecycle_integrity_and_rollback(monkeypatch) -> None:
    """使用 PostgreSQL 验证撤回授权、运行时完整性、回滚及保存后重新提交。"""
    from app.core.config import get_settings

    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)

    async def ensure_postgres_is_available() -> None:
        """确认真实 PostgreSQL 可连接；本机未运行时保留集成测试为 skipped。"""
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as error:
            await engine.dispose()
            pytest.skip(f"PostgreSQL integration service unavailable: {type(error).__name__}")

    asyncio.run(ensure_postgres_is_available())
    # AsyncEngine 池中的连接绑定事件循环，dispose 后再由集成测试独立循环使用。
    asyncio.run(engine.dispose())
    workflow_code = f"withdraw_{uuid4().hex}"
    applicant_id = str(uuid4())
    other_id = str(uuid4())
    manager_id = str(uuid4())
    seeded_ids: list[UUID] = []
    entity_keys: list[str] = []
    workflow_v1 = WorkflowDefinitionSpec(
        code=workflow_code,
        version=1,
        nodes=(
            WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
            WorkflowNodeDefinition("data_admin_review", UserRole.DATA_ADMIN),
        ),
    )
    workflow_v2 = WorkflowDefinitionSpec(
        code=workflow_code,
        version=2,
        nodes=(
            WorkflowNodeDefinition("data_admin_review", UserRole.DATA_ADMIN),
            WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
        ),
    )

    async def add_case(
        session,
        *,
        status: ApprovalStatus = ApprovalStatus.IN_APPROVAL,
        node: str | None = "manager_review",
        include_history: bool = True,
        applicant: str = applicant_id,
        task_operator: str | None = None,
        workflow_version: int | None = 1,
        workflow_instance: str | None = "instance",
    ) -> tuple[UUID, UUID, str, datetime]:
        """写入孤立随机审批案例及可选当前任务创建历史。"""
        approval_id, change_id = uuid4(), uuid4()
        task_id = str(uuid4()) if node is not None else None
        submitted_at = datetime.now(UTC)
        application = ApprovalApplication(
            id=approval_id,
            business_type=BusinessType.MASTER_DATA_CHANGE,
            business_id=str(change_id),
            applicant_id=applicant,
            status=status,
            workflow_code=workflow_code if workflow_version is not None else None,
            workflow_version=workflow_version,
            workflow_instance_id=workflow_instance,
            current_task_id=task_id,
            current_node_code=node,
            current_node_name="first display snapshot" if node is not None else None,
            submitted_at=submitted_at,
        )
        entity_key = f"withdraw-{uuid4().hex}"
        change = MasterDataChange(
            id=change_id,
            approval_id=approval_id,
            entity_type="supplier",
            entity_key=entity_key,
            before_data={"name": "before"},
            base_entity_version=4,
            form_data={"name": "after"},
            change_items=[{"field": "name", "operation": "UPDATE"}],
            change_reason="withdraw test",
        )
        entity = MasterDataEntity(
            entity_type="supplier",
            entity_key=entity_key,
            data_json={"name": "current"},
            version=4,
        )
        session.add_all([application, change, entity])
        if include_history and task_id is not None:
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
        await session.flush()
        seeded_ids.append(approval_id)
        entity_keys.append(entity_key)
        return approval_id, change_id, task_id or "", submitted_at

    async def run() -> None:
        """执行 PostgreSQL 场景并在 finally 中移除所有测试数据。"""
        try:
            async with factory() as session:
                from app.domain.workflow.definition import workflow_definition_to_dict

                session.add_all(
                    [
                        WorkflowDefinition(
                            code=workflow_code,
                            version=1,
                            definition_json=workflow_definition_to_dict(workflow_v1),
                            active=False,
                        ),
                        WorkflowDefinition(
                            code=workflow_code,
                            version=2,
                            definition_json=workflow_definition_to_dict(workflow_v2),
                            active=True,
                        ),
                    ]
                )
                await session.flush()
                success_id, success_change_id, old_task, old_submitted = await add_case(session)
                targeted_id, _, targeted_task, _ = await add_case(session, task_operator=manager_id)
                later_id, _, _, _ = await add_case(session, node="data_admin_review")
                wrong_owner_id, _, _, _ = await add_case(session, applicant=other_id)
                assigned_non_owner_id, _, _, _ = await add_case(session, task_operator=other_id)
                missing_change_id, missing_change_pk, _, _ = await add_case(session)
                mismatch_id, _, _, _ = await add_case(session)
                missing_binding_id, _, _, _ = await add_case(session)
                missing_instance_id, _, _, _ = await add_case(session)
                missing_task_id, _, _, _ = await add_case(session)
                missing_node_id, _, _, _ = await add_case(session)
                unknown_node_id, _, _, _ = await add_case(session, node="unknown_node")
                missing_history_id, _, _, _ = await add_case(session, include_history=False)
                foreign_history_id, _, foreign_task, _ = await add_case(
                    session, include_history=False
                )
                foreign_holder_id, _, _, _ = await add_case(session)
                corrupt_definition_id, _, _, _ = await add_case(session)
                (
                    rollback_id,
                    rollback_change_id,
                    rollback_task,
                    rollback_submitted,
                ) = await add_case(session)
                draft_id, _, _, _ = await add_case(
                    session,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                )
                completed_id, _, _, _ = await add_case(
                    session,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                )
                voided_id, _, _, _ = await add_case(
                    session,
                    status=ApprovalStatus.VOIDED,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                )
                session.add(
                    WorkflowDefinition(
                        code=workflow_code,
                        version=3,
                        definition_json={"broken": True},
                        active=False,
                    )
                )
                await session.flush()
                targeted_record = ApprovalRecord(
                    approval_id=targeted_id,
                    operator_id=manager_id,
                    action=ApprovalAction.REJECT,
                    from_status=ApprovalStatus.IN_APPROVAL,
                    to_status=ApprovalStatus.IN_APPROVAL,
                    target_task_id=targeted_task,
                    target_node_code="manager_review",
                    target_operator_id=manager_id,
                )
                progressed_task = str(uuid4())
                progressed_record = ApprovalRecord(
                    approval_id=targeted_id,
                    operator_id=manager_id,
                    action=ApprovalAction.APPROVE,
                    from_status=ApprovalStatus.IN_APPROVAL,
                    to_status=ApprovalStatus.IN_APPROVAL,
                    from_task_id=str(uuid4()),
                    from_node_code="manager_review",
                    target_task_id=progressed_task,
                    target_node_code="data_admin_review",
                )
                session.add_all([progressed_record, targeted_record])
                await session.flush()
                mismatch = await session.get(ApprovalApplication, mismatch_id)
                missing_binding = await session.get(ApprovalApplication, missing_binding_id)
                missing_instance = await session.get(ApprovalApplication, missing_instance_id)
                missing_task = await session.get(ApprovalApplication, missing_task_id)
                missing_node = await session.get(ApprovalApplication, missing_node_id)
                corrupt = await session.get(ApprovalApplication, corrupt_definition_id)
                assert all(
                    value is not None
                    for value in (
                        mismatch,
                        missing_binding,
                        missing_instance,
                        missing_task,
                        missing_node,
                        corrupt,
                    )
                )
                mismatch.business_id = str(uuid4())
                missing_binding.workflow_code = None
                missing_binding.workflow_version = None
                missing_instance.workflow_instance_id = None
                missing_task.current_task_id = None
                missing_node.current_node_code = None
                corrupt.workflow_version = 3
                session.add(
                    ApprovalRecord(
                        approval_id=foreign_holder_id,
                        operator_id=manager_id,
                        action=ApprovalAction.SUBMIT,
                        from_status=ApprovalStatus.DRAFT,
                        to_status=ApprovalStatus.IN_APPROVAL,
                        target_task_id=foreign_task,
                        target_node_code="manager_review",
                    )
                )
                await session.flush()
                await session.execute(
                    delete(MasterDataChange).where(MasterDataChange.id == missing_change_pk)
                )
                await session.commit()

            # 使用 PostgreSQL 中持久化的 applicant ID 与真实用户上下文一致。
            applicant_auth = AuthContext(
                session_id=uuid4(),
                user_id=UUID(applicant_id),
                username="withdraw-applicant",
                display_name="Withdraw Applicant",
                roles=frozenset({UserRole.APPLICANT}),
            )
            async with factory() as session:
                result = await ApprovalService(session).withdraw(
                    success_id, applicant_auth, "  please revise  "
                )
                assert result.action is ApprovalAction.WITHDRAW
                assert result.status is ApprovalStatus.DRAFT
                assert result.current_task_id is None and result.current_node_code is None
                assert result.workflow_version == 1
            async with factory() as verify:
                application = await verify.get(ApprovalApplication, success_id)
                change = await verify.get(MasterDataChange, success_change_id)
                entity = await verify.scalar(
                    select(MasterDataEntity).where(MasterDataEntity.entity_key == entity_keys[0])
                )
                record = await verify.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.approval_id == success_id,
                        ApprovalRecord.action == ApprovalAction.WITHDRAW,
                    )
                )
                assert application is not None and change is not None and entity is not None
                assert application.status is ApprovalStatus.DRAFT
                assert application.workflow_code == workflow_code
                assert application.workflow_version == 1
                assert application.workflow_instance_id == "instance"
                assert application.current_task_id is None
                assert (
                    application.current_node_code is None and application.current_node_name is None
                )
                assert application.submitted_at == old_submitted
                assert application.completed_at is None and application.version == 1
                assert change.before_data == {"name": "before"}
                assert change.base_entity_version == 4
                assert change.form_data == {"name": "after"}
                assert change.change_items == [{"field": "name", "operation": "UPDATE"}]
                assert entity.data_json == {"name": "current"} and entity.version == 4
                assert record is not None
                assert record.operator_id == applicant_id
                assert record.from_status is ApprovalStatus.IN_APPROVAL
                assert record.to_status is ApprovalStatus.DRAFT
                assert record.from_task_id == old_task
                assert record.from_node_code == "manager_review"
                assert record.from_node_name == "first display snapshot"
                assert record.target_task_id is None and record.target_node_code is None
                assert record.target_node_name is None and record.target_operator_id == applicant_id
                assert record.comment == "please revise"

            # 同一个首节点的 targeted-return task 仍可由申请人撤回；目标处理人不授权。
            async with factory() as session:
                await ApprovalService(session).withdraw(targeted_id, applicant_auth, "   ")
            async with factory() as verify:
                target_withdraw = await verify.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.approval_id == targeted_id,
                        ApprovalRecord.action == ApprovalAction.WITHDRAW,
                    )
                )
                assert target_withdraw is not None and target_withdraw.comment is None
                assert target_withdraw.from_task_id == targeted_task

            expected_errors = {
                later_id: ApprovalErrorCode.WITHDRAW_NOT_ALLOWED,
                wrong_owner_id: ApprovalErrorCode.FORBIDDEN,
                missing_change_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                mismatch_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                missing_binding_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                missing_instance_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                missing_task_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                missing_node_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                unknown_node_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                missing_history_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                foreign_history_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                corrupt_definition_id: ApprovalErrorCode.DATA_INTEGRITY_ERROR,
                draft_id: ApprovalErrorCode.INVALID_TRANSITION,
                completed_id: ApprovalErrorCode.INVALID_TRANSITION,
                voided_id: ApprovalErrorCode.INVALID_TRANSITION,
                uuid4(): ApprovalErrorCode.NOT_FOUND,
            }
            for approval_id, expected_error in expected_errors.items():
                async with factory() as session:
                    with pytest.raises(ApprovalError) as error:
                        await ApprovalService(session).withdraw(approval_id, applicant_auth)
                    assert error.value.code == expected_error.value

            task_operator_auth = AuthContext(
                session_id=uuid4(),
                user_id=UUID(other_id),
                username="assigned-reviewer",
                display_name="Assigned Reviewer",
                roles=frozenset({UserRole.APPLICANT}),
            )
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).withdraw(
                        assigned_non_owner_id, task_operator_auth
                    )
                assert error.value.code == ApprovalErrorCode.FORBIDDEN.value

            async with factory() as session:
                service = ApprovalService(session)

                async def missing_exact_version(code, version):
                    """模拟绑定版本缺失，确保撤回不回退 active 流程。"""
                    assert code == workflow_code and version == 1
                    return None

                monkeypatch.setattr(
                    service._workflow_repository, "get_by_code_version", missing_exact_version
                )
                with pytest.raises(ApprovalError) as error:
                    await service.withdraw(rollback_id, applicant_auth)
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value

            # 用于回滚的合法案例：记录 flush 后注入失败，状态与历史必须一并回滚。
            original_save = ApprovalRepository.save_transition

            async def flush_then_fail(repository, record) -> None:
                """先 flush WITHDRAW 记录，再注入故障验证事务整体回滚。"""
                await original_save(repository, record)
                raise RuntimeError("injected withdraw failure")

            monkeypatch.setattr(ApprovalRepository, "save_transition", flush_then_fail)
            with pytest.raises(RuntimeError, match="injected withdraw failure"):
                async with factory() as session:
                    await ApprovalService(session).withdraw(rollback_id, applicant_auth)
            monkeypatch.setattr(ApprovalRepository, "save_transition", original_save)
            async with factory() as verify:
                rollback_application = await verify.get(ApprovalApplication, rollback_id)
                assert rollback_application is not None
                assert rollback_application.status is ApprovalStatus.IN_APPROVAL
                assert rollback_application.current_task_id == rollback_task
                assert rollback_application.current_node_code == "manager_review"
                assert rollback_application.current_node_name == "first display snapshot"
                assert rollback_application.workflow_version == 1
                assert rollback_application.workflow_instance_id == "instance"
                assert rollback_application.submitted_at == rollback_submitted
                assert rollback_application.version == 1
                rollback_count = await verify.scalar(
                    select(func.count())
                    .select_from(ApprovalRecord)
                    .where(
                        ApprovalRecord.approval_id == rollback_id,
                        ApprovalRecord.action == ApprovalAction.WITHDRAW,
                    )
                )
                assert rollback_count == 0
                rollback_change = await verify.get(MasterDataChange, rollback_change_id)
                assert rollback_change is not None
                rollback_entity = await verify.scalar(
                    select(MasterDataEntity).where(
                        MasterDataEntity.entity_key == rollback_change.entity_key
                    )
                )
                assert rollback_change.form_data == {"name": "after"}
                assert rollback_change.before_data == {"name": "before"}
                assert rollback_change.base_entity_version == 4
                assert rollback_entity is not None
                assert rollback_entity.data_json == {"name": "current"}
                assert rollback_entity.version == 4

            # 撤回后通过既有 SAVE 与 SUBMIT 服务继续处理，仍使用绑定 v1 而非 active v2。
            async with factory() as session:
                saved = await MasterDataService(session).create_or_update_draft(
                    "supplier",
                    entity_keys[0],
                    ChangeDraftRequest(
                        draft_approval_id=success_id,
                        changes={"name": "edited after withdraw"},
                        reason="withdraw and edit",
                    ),
                    applicant_auth,
                )
                assert saved.approval_id == success_id
            async with factory() as session:
                resubmitted = await ApprovalService(session).submit(success_id, applicant_auth)
                assert resubmitted.status is ApprovalStatus.IN_APPROVAL
                assert resubmitted.workflow_code == workflow_code
                assert resubmitted.workflow_version == 1
                assert resubmitted.current_node_code == "manager_review"
                assert resubmitted.current_task_id != old_task
                assert resubmitted.workflow_instance_id != "instance"
                assert resubmitted.submitted_at != old_submitted
            async with factory() as verify:
                resubmitted_application = await verify.get(ApprovalApplication, success_id)
                preserved_withdraw = await verify.scalar(
                    select(ApprovalRecord).where(
                        ApprovalRecord.approval_id == success_id,
                        ApprovalRecord.action == ApprovalAction.WITHDRAW,
                    )
                )
                assert resubmitted_application is not None
                assert resubmitted_application.workflow_version == 1
                assert resubmitted_application.current_task_id == resubmitted.current_task_id
                assert (
                    resubmitted_application.workflow_instance_id == resubmitted.workflow_instance_id
                )
                assert resubmitted_application.submitted_at == resubmitted.submitted_at
                assert preserved_withdraw is not None
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
                        delete(MasterDataEntity).where(MasterDataEntity.entity_key.in_(entity_keys))
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
