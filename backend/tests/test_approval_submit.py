"""Approval submit API 与 PostgreSQL 事务集成测试。"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_approval_service, get_auth_service
from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.schemas import SubmitApprovalResponse
from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.infrastructure.database import create_db_engine, create_session_factory
from app.main import create_app
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange, MasterDataEntity
from app.models.workflow import WorkflowDefinition
from app.workflow.repository import WorkflowDefinitionRepository


def test_submit_api_requires_bearer_and_returns_request_id_error_envelope() -> None:
    """验证匿名提交返回带 Bearer challenge 和 request ID 的统一错误。"""
    settings = Settings(_env_file=None)
    api = create_app(settings)
    api.dependency_overrides[get_auth_service] = lambda: object()
    client = TestClient(RequestIdMiddleware(api, settings))
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/submit", headers={"X-Request-ID": "submit-auth"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
    assert response.json()["error"]["request_id"] == "submit-auth"


@pytest.mark.parametrize(
    ("roles", "expected_status", "expected_code", "service_error_code", "service_called"),
    [
        (
            {UserRole.APPROVER},
            403,
            "AUTH_PERMISSION_DENIED",
            ApprovalErrorCode.INVALID_TRANSITION,
            False,
        ),
        (
            {UserRole.DATA_ADMIN},
            403,
            "AUTH_PERMISSION_DENIED",
            ApprovalErrorCode.INVALID_TRANSITION,
            False,
        ),
        (
            {UserRole.ADMIN},
            403,
            "AUTH_PERMISSION_DENIED",
            ApprovalErrorCode.INVALID_TRANSITION,
            False,
        ),
        (
            {UserRole.APPLICANT},
            409,
            "APPROVAL_INVALID_TRANSITION",
            ApprovalErrorCode.INVALID_TRANSITION,
            True,
        ),
        (
            {UserRole.APPLICANT},
            403,
            "APPROVAL_FORBIDDEN",
            ApprovalErrorCode.FORBIDDEN,
            True,
        ),
        (
            {UserRole.APPLICANT},
            404,
            "APPROVAL_NOT_FOUND",
            ApprovalErrorCode.NOT_FOUND,
            True,
        ),
        (
            {UserRole.APPLICANT},
            409,
            "APPROVAL_WORKFLOW_NOT_CONFIGURED",
            ApprovalErrorCode.WORKFLOW_NOT_CONFIGURED,
            True,
        ),
    ],
)
def test_submit_api_role_guard_and_error_mapping(
    roles, expected_status, expected_code, service_error_code, service_called
) -> None:
    """验证 APPLICANT 显式角色门禁及审批错误的统一 HTTP 映射。"""
    from app.auth.context import AuthContext

    auth_context = AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        username="demo",
        display_name="Demo",
        roles=frozenset(roles),
    )

    class FakeAuthService:
        """提供 API 测试使用的可信认证上下文。"""

        async def authenticate(self, token: str) -> AuthContext:
            """返回测试指定身份，不执行数据库访问。"""
            assert token == "test-token"
            return auth_context

    class FakeApprovalService:
        """在 HTTP 边界触发目标审批错误。"""

        def __init__(self) -> None:
            """初始化服务调用计数，供权限绕过断言使用。"""
            self.calls = 0

        async def submit(self, approval_id, current_auth):
            """验证服务只收到可信身份，再返回稳定审批错误。"""
            self.calls += 1
            assert current_auth is auth_context
            raise ApprovalError(service_error_code)

    settings = Settings(_env_file=None)
    api = create_app(settings)
    service_stub = FakeApprovalService()
    api.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    api.dependency_overrides[get_approval_service] = lambda: service_stub
    client = TestClient(RequestIdMiddleware(api, settings))
    response = client.post(
        f"/api/v1/approvals/{uuid4()}/submit",
        headers={"Authorization": "Bearer test-token", "X-Request-ID": "submit-role"},
        json={"workflow_code": "attacker", "workflow_instance_id": "injected"},
    )
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["request_id"] == "submit-role"
    assert service_stub.calls == int(service_called)


def test_submit_api_owner_success_ignores_client_workflow_values() -> None:
    """验证 APPLICANT 可调用提交端点且客户端 workflow/runtime 不进入服务。"""
    from datetime import UTC, datetime

    from app.auth.context import AuthContext

    auth_context = AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        username="applicant",
        display_name="Applicant",
        roles=frozenset({UserRole.APPLICANT}),
    )
    result = SubmitApprovalResponse(
        approval_id=uuid4(),
        status=ApprovalStatus.IN_APPROVAL,
        workflow_code="supplier_change",
        workflow_version=1,
        workflow_instance_id=str(uuid4()),
        current_task_id=str(uuid4()),
        current_node_code="manager_review",
        current_node_role=UserRole.APPROVER,
        submitted_at=datetime.now(UTC),
    )

    class FakeAuthService:
        """提供可信 APPLICANT 身份以隔离 API 路由测试。"""

        async def authenticate(self, token: str) -> AuthContext:
            """校验测试 Token 并返回服务端身份。"""
            assert token == "test-token"
            return auth_context

    class FakeApprovalService:
        """返回服务端构建的提交结果并检查身份来源。"""

        async def submit(self, approval_id, current_auth):
            """忽略客户端 body，仅按路径 ID 和可信身份调用。"""
            assert current_auth is auth_context
            assert approval_id == result.approval_id
            return result

    settings = Settings(_env_file=None)
    api = create_app(settings)
    api.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    api.dependency_overrides[get_approval_service] = lambda: FakeApprovalService()
    client = TestClient(RequestIdMiddleware(api, settings))
    response = client.post(
        f"/api/v1/approvals/{result.approval_id}/submit",
        headers={"Authorization": "Bearer test-token"},
        json={"workflow_code": "attacker", "current_task_id": "attacker"},
    )
    assert response.status_code == 200
    assert response.json()["workflow_code"] == "supplier_change"
    assert response.json()["workflow_version"] == 1
    assert response.json()["current_task_id"] == result.current_task_id


def test_postgres_submit_binding_resubmit_and_rollback(monkeypatch) -> None:
    """验证绑定、重复提交、精确版本重提交流程及 flush 后事务回滚。"""
    from app.approval.service import ApprovalService

    settings = Settings(_env_file=None)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    applicant_id = uuid4()
    approval_id = uuid4()
    change_id = uuid4()
    entity_key = f"submit-{uuid4().hex}"
    workflow_code = "supplier_change"
    workflow_version = 900000 + int(uuid4().hex[:6], 16)
    spec = WorkflowDefinitionSpec(
        code=workflow_code,
        version=workflow_version,
        nodes=(WorkflowNodeDefinition("manager_review", UserRole.APPROVER),),
    )

    async def run() -> None:
        """准备真实 PostgreSQL 数据并验证多个提交事务分支。"""
        try:
            async with factory() as session:
                session.add(
                    MasterDataEntity(
                        entity_type="supplier",
                        entity_key=entity_key,
                        data_json={"name": "before"},
                    )
                )
                session.add(
                    ApprovalApplication(
                        id=approval_id,
                        business_type=BusinessType.MASTER_DATA_CHANGE,
                        business_id=str(change_id),
                        applicant_id=str(applicant_id),
                        status=ApprovalStatus.DRAFT,
                    )
                )
                session.add(
                    MasterDataChange(
                        id=change_id,
                        approval_id=approval_id,
                        entity_type="supplier",
                        entity_key=entity_key,
                        before_data={"name": "before"},
                        base_entity_version=1,
                        form_data={"name": "after"},
                        change_items=[{"field": "name", "operation": "UPDATE"}],
                    )
                )
                await session.commit()

            from app.auth.context import AuthContext

            auth = AuthContext(
                session_id=uuid4(),
                user_id=applicant_id,
                username="applicant",
                display_name="Applicant",
                roles=frozenset({UserRole.APPLICANT}),
            )

            async with factory() as session:
                with pytest.raises(ApprovalError) as workflow_error:
                    await ApprovalService(session).submit(approval_id, auth)
                assert workflow_error.value.code == ApprovalErrorCode.WORKFLOW_NOT_CONFIGURED.value
            async with factory() as session:
                session.add(
                    WorkflowDefinition(
                        code=spec.code,
                        version=spec.version,
                        definition_json={
                            "code": spec.code,
                            "version": spec.version,
                            "nodes": [{"key": "manager_review", "role": "APPROVER"}],
                        },
                        active=True,
                    )
                )
                await session.commit()

            original_save = ApprovalRepository.save_transition

            async def save_then_fail(repository, record) -> None:
                """先 flush SUBMIT 历史后抛错，检验首次绑定和运行时一并回滚。"""
                await original_save(repository, record)
                raise RuntimeError("injected initial submit failure")

            monkeypatch.setattr(ApprovalRepository, "save_transition", save_then_fail)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="injected initial submit failure"):
                    await ApprovalService(session).submit(approval_id, auth)
            async with factory() as session:
                approval = await session.get(ApprovalApplication, approval_id)
                assert approval is not None
                assert approval.status is ApprovalStatus.DRAFT
                assert approval.workflow_code is None
                assert approval.workflow_version is None
                assert approval.workflow_instance_id is None
                assert approval.current_task_id is None
                assert approval.submitted_at is None
                assert (
                    await session.scalar(
                        select(ApprovalRecord.id).where(
                            ApprovalRecord.approval_id == approval_id,
                            ApprovalRecord.action == ApprovalAction.SUBMIT,
                        )
                    )
                    is None
                )
            monkeypatch.setattr(ApprovalRepository, "save_transition", original_save)

            other_applicant = AuthContext(
                session_id=uuid4(),
                user_id=uuid4(),
                username="other",
                display_name="Other",
                roles=frozenset({UserRole.APPLICANT}),
            )
            async with factory() as session:
                with pytest.raises(ApprovalError) as missing_error:
                    await ApprovalService(session).submit(uuid4(), auth)
                assert missing_error.value.code == ApprovalErrorCode.NOT_FOUND.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as owner_error:
                    await ApprovalService(session).submit(approval_id, other_applicant)
                assert owner_error.value.code == ApprovalErrorCode.FORBIDDEN.value
            async with factory() as session:
                assert (
                    await session.scalar(
                        select(ApprovalRecord.id).where(
                            ApprovalRecord.approval_id == approval_id,
                            ApprovalRecord.action == ApprovalAction.SUBMIT,
                        )
                    )
                    is None
                )

            async with factory() as session:
                response = await ApprovalService(session).submit(approval_id, auth)
            assert response.status is ApprovalStatus.IN_APPROVAL
            assert response.workflow_code == workflow_code
            assert response.workflow_version == workflow_version
            assert response.current_node_code == "manager_review"
            assert response.current_node_role is UserRole.APPROVER

            async with factory() as session:
                approval = await session.get(ApprovalApplication, approval_id)
                assert approval is not None
                assert approval.workflow_code == workflow_code
                assert approval.workflow_version == workflow_version
                assert approval.workflow_instance_id
                assert approval.current_task_id
                assert approval.current_node_name is None
                assert approval.submitted_at is not None
                assert approval.submitted_at.tzinfo is not None
                assert approval.version == 1
                records = list(
                    (
                        await session.scalars(
                            select(ApprovalRecord).where(ApprovalRecord.approval_id == approval_id)
                        )
                    ).all()
                )
                submits = [record for record in records if record.action is ApprovalAction.SUBMIT]
                assert len(submits) == 1
                assert submits[0].from_status is ApprovalStatus.DRAFT
                assert submits[0].to_status is ApprovalStatus.IN_APPROVAL
                assert submits[0].target_task_id == approval.current_task_id
                assert submits[0].target_node_code == "manager_review"
                assert submits[0].target_node_name is None
                assert submits[0].target_operator_id is None

            first_runtime: tuple[str, str] | None = None
            async with factory() as session:
                approval = await session.get(ApprovalApplication, approval_id)
                assert approval is not None
                first_runtime = (approval.workflow_instance_id, approval.current_task_id)
            async with factory() as session:
                with pytest.raises(ApprovalError) as duplicate_error:
                    await ApprovalService(session).submit(approval_id, auth)
                assert duplicate_error.value.code == ApprovalErrorCode.INVALID_TRANSITION.value

            async with factory() as session:
                approval = await session.get(ApprovalApplication, approval_id)
                assert approval is not None
                assert (approval.workflow_instance_id, approval.current_task_id) == first_runtime
                assert (
                    len(
                        list(
                            (
                                await session.scalars(
                                    select(ApprovalRecord).where(
                                        ApprovalRecord.approval_id == approval_id,
                                        ApprovalRecord.action == ApprovalAction.SUBMIT,
                                    )
                                )
                            ).all()
                        )
                    )
                    == 1
                )

            async with factory() as session:
                active_v1 = await session.scalar(
                    select(WorkflowDefinition).where(
                        WorkflowDefinition.code == workflow_code,
                        WorkflowDefinition.version == workflow_version,
                    )
                )
                assert active_v1 is not None
                active_v1.active = False
                session.add(
                    WorkflowDefinition(
                        code=workflow_code,
                        version=workflow_version + 1,
                        definition_json={
                            "code": workflow_code,
                            "version": workflow_version + 1,
                            "nodes": [{"key": "new_review", "role": "DATA_ADMIN"}],
                        },
                        active=True,
                    )
                )
                await session.execute(
                    update(ApprovalApplication)
                    .where(ApprovalApplication.id == approval_id)
                    .values(status=ApprovalStatus.DRAFT)
                )
                await session.commit()

            original_bound_lookup = WorkflowDefinitionRepository.get_by_code_version

            async def missing_bound_version(repository, code: str, version: int):
                """模拟已绑定版本无法读取，验证服务不会回退到新 active 版本。"""
                return None

            monkeypatch.setattr(
                WorkflowDefinitionRepository,
                "get_by_code_version",
                missing_bound_version,
            )
            async with factory() as session:
                with pytest.raises(ApprovalError) as missing_bound_error:
                    await ApprovalService(session).submit(approval_id, auth)
                assert (
                    missing_bound_error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
                )
            monkeypatch.setattr(
                WorkflowDefinitionRepository,
                "get_by_code_version",
                original_bound_lookup,
            )

            async with factory() as session:
                resubmitted = await ApprovalService(session).submit(approval_id, auth)
            assert resubmitted.workflow_version == workflow_version
            assert resubmitted.current_node_code == "manager_review"
            async with factory() as session:
                approval = await session.get(ApprovalApplication, approval_id)
                assert approval is not None
                assert (approval.workflow_instance_id, approval.current_task_id) != first_runtime
                assert approval.version == 1
                assert approval.workflow_version == workflow_version
                assert (
                    await session.scalar(
                        select(WorkflowDefinition.version).where(
                            WorkflowDefinition.code == workflow_code,
                            WorkflowDefinition.active.is_(True),
                        )
                    )
                    == workflow_version + 1
                )
                submit_records = list(
                    (
                        await session.scalars(
                            select(ApprovalRecord)
                            .where(
                                ApprovalRecord.approval_id == approval_id,
                                ApprovalRecord.action == ApprovalAction.SUBMIT,
                            )
                            .order_by(ApprovalRecord.created_at)
                        )
                    ).all()
                )
                assert len(submit_records) == 2
                assert submit_records[1].from_task_id == first_runtime[1]
                assert submit_records[1].from_node_code == "manager_review"
                count_before_failure = len(
                    list(
                        (
                            await session.scalars(
                                select(ApprovalRecord).where(
                                    ApprovalRecord.approval_id == approval_id,
                                    ApprovalRecord.action == ApprovalAction.SUBMIT,
                                )
                            )
                        ).all()
                    )
                )

            rollback_time = datetime.now(UTC)
            async with factory() as session:
                await session.execute(
                    update(ApprovalApplication)
                    .where(ApprovalApplication.id == approval_id)
                    .values(
                        status=ApprovalStatus.DRAFT,
                        workflow_instance_id="rollback-instance",
                        current_task_id="rollback-task",
                        current_node_code="rollback-node",
                        current_node_name="rollback-name",
                        submitted_at=rollback_time,
                    )
                )
                await session.commit()

            async def save_then_fail(repository, record) -> None:
                """先真实 flush SUBMIT 历史，再注入异常测试整个事务回滚。"""
                await original_save(repository, record)
                raise RuntimeError("injected submit failure")

            monkeypatch.setattr(ApprovalRepository, "save_transition", save_then_fail)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="injected submit failure"):
                    await ApprovalService(session).submit(approval_id, auth)

            async with factory() as session:
                approval = await session.get(ApprovalApplication, approval_id)
                assert approval is not None
                assert approval.status is ApprovalStatus.DRAFT
                assert approval.workflow_code == workflow_code
                assert approval.workflow_version == workflow_version
                assert approval.workflow_instance_id == "rollback-instance"
                assert approval.current_task_id == "rollback-task"
                assert approval.current_node_code == "rollback-node"
                assert approval.current_node_name == "rollback-name"
                assert approval.submitted_at == rollback_time
                records = list(
                    (
                        await session.scalars(
                            select(ApprovalRecord).where(
                                ApprovalRecord.approval_id == approval_id,
                                ApprovalRecord.action == ApprovalAction.SUBMIT,
                            )
                        )
                    ).all()
                )
                assert len(records) == count_before_failure
                entity = await session.scalar(
                    select(MasterDataEntity).where(
                        MasterDataEntity.entity_type == "supplier",
                        MasterDataEntity.entity_key == entity_key,
                    )
                )
                change = await session.get(MasterDataChange, change_id)
                assert entity is not None and entity.version == 1
                assert entity.data_json == {"name": "before"}
                assert change is not None
                assert change.before_data == {"name": "before"}
                assert change.form_data == {"name": "after"}
                assert change.base_entity_version == 1
        finally:
            async with factory() as session:
                await session.execute(
                    delete(ApprovalRecord).where(ApprovalRecord.approval_id == approval_id)
                )
                await session.execute(
                    delete(MasterDataChange).where(MasterDataChange.id == change_id)
                )
                await session.execute(
                    delete(ApprovalApplication).where(ApprovalApplication.id == approval_id)
                )
                await session.execute(
                    delete(MasterDataEntity).where(
                        MasterDataEntity.entity_type == "supplier",
                        MasterDataEntity.entity_key == entity_key,
                    )
                )
                await session.execute(
                    delete(WorkflowDefinition).where(
                        WorkflowDefinition.code == workflow_code,
                        WorkflowDefinition.version.in_([workflow_version, workflow_version + 1]),
                    )
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())


def test_postgres_workflow_binding_constraints_reject_invalid_pairs() -> None:
    """验证真实 PostgreSQL 执行绑定 pair、正版本和 composite FK 约束。"""
    settings = Settings(_env_file=None)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)

    async def run() -> None:
        """分别尝试非法绑定并确认约束错误后回滚 Session。"""
        try:
            async with factory() as session:
                for code, version in (("supplier_change", None), (None, 1)):
                    with pytest.raises(IntegrityError):
                        await session.execute(
                            text(
                                "INSERT INTO approval_application "
                                "(id, business_type, business_id, applicant_id, status, "
                                "version, workflow_code, workflow_version) "
                                "VALUES (:id, 'MASTER_DATA_CHANGE', :business_id, 'test', "
                                "'DRAFT', 1, :code, :workflow_version)"
                            ),
                            {
                                "id": uuid4(),
                                "business_id": str(uuid4()),
                                "code": code,
                                "workflow_version": version,
                            },
                        )
                    await session.rollback()

                with pytest.raises(IntegrityError):
                    await session.execute(
                        text(
                            "INSERT INTO approval_application "
                            "(id, business_type, business_id, applicant_id, status, version, "
                            "workflow_code, workflow_version) VALUES "
                            "(:id, 'MASTER_DATA_CHANGE', :business_id, 'test', 'DRAFT', 1, "
                            "'supplier_change', 0)"
                        ),
                        {"id": uuid4(), "business_id": str(uuid4())},
                    )
                await session.rollback()

                missing_code = f"missing_{uuid4().hex}"
                with pytest.raises(IntegrityError) as foreign_key_error:
                    await session.execute(
                        text(
                            "INSERT INTO approval_application "
                            "(id, business_type, business_id, applicant_id, status, version, "
                            "workflow_code, workflow_version) VALUES "
                            "(:id, 'MASTER_DATA_CHANGE', :business_id, 'test', 'DRAFT', 1, "
                            ":code, 1)"
                        ),
                        {
                            "id": uuid4(),
                            "business_id": str(uuid4()),
                            "code": missing_code,
                        },
                    )
                assert "fk_approval_application_workflow_definition" in str(
                    foreign_key_error.value.orig
                )
                await session.rollback()

                constraint_names = set(
                    (
                        await session.scalars(
                            text(
                                "SELECT conname FROM pg_constraint "
                                "WHERE conrelid = 'approval_application'::regclass"
                            )
                        )
                    ).all()
                )
                assert "ck_approval_application_workflow_binding_pair" in constraint_names
                assert "ck_approval_application_workflow_version_positive" in constraint_names
                assert "fk_approval_application_workflow_definition" in constraint_names
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_submit_rejects_broken_business_and_workflow_links() -> None:
    """验证提交拒绝缺失或不一致的业务关联及损坏的已绑定流程快照。"""
    from app.approval.service import ApprovalService
    from app.auth.context import AuthContext

    settings = Settings(_env_file=None)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    applicant_id = uuid4()
    missing_change_approval_id = uuid4()
    mismatch_approval_id = uuid4()
    mismatch_change_id = uuid4()
    unsupported_approval_id = uuid4()
    unsupported_change_id = uuid4()
    corrupt_approval_id = uuid4()
    corrupt_change_id = uuid4()
    corrupt_workflow_code = f"corrupt_{uuid4().hex}"
    approval_ids = (
        missing_change_approval_id,
        mismatch_approval_id,
        unsupported_approval_id,
        corrupt_approval_id,
    )
    change_ids = (mismatch_change_id, unsupported_change_id, corrupt_change_id)
    auth = AuthContext(
        session_id=uuid4(),
        user_id=applicant_id,
        username="applicant",
        display_name="Applicant",
        roles=frozenset({UserRole.APPLICANT}),
    )

    async def run() -> None:
        """写入真实 PostgreSQL 关联数据并验证错误路径不产生提交历史。"""
        try:
            async with factory() as session:
                session.add(
                    WorkflowDefinition(
                        code=corrupt_workflow_code,
                        version=1,
                        definition_json={"corrupt": True},
                        active=False,
                    )
                )
                await session.flush()
                session.add_all(
                    [
                        ApprovalApplication(
                            id=missing_change_approval_id,
                            business_type=BusinessType.MASTER_DATA_CHANGE,
                            business_id=str(uuid4()),
                            applicant_id=str(applicant_id),
                            status=ApprovalStatus.DRAFT,
                        ),
                        ApprovalApplication(
                            id=mismatch_approval_id,
                            business_type=BusinessType.MASTER_DATA_CHANGE,
                            business_id=str(uuid4()),
                            applicant_id=str(applicant_id),
                            status=ApprovalStatus.DRAFT,
                        ),
                        MasterDataChange(
                            id=mismatch_change_id,
                            approval_id=mismatch_approval_id,
                            entity_type="supplier",
                            entity_key=f"mismatch-{uuid4().hex}",
                            before_data={"name": "before"},
                            base_entity_version=1,
                            form_data={"name": "after"},
                            change_items=[{"field": "name", "operation": "UPDATE"}],
                        ),
                        ApprovalApplication(
                            id=unsupported_approval_id,
                            business_type=BusinessType.MASTER_DATA_CHANGE,
                            business_id=str(unsupported_change_id),
                            applicant_id=str(applicant_id),
                            status=ApprovalStatus.DRAFT,
                        ),
                        MasterDataChange(
                            id=unsupported_change_id,
                            approval_id=unsupported_approval_id,
                            entity_type="customer",
                            entity_key=f"customer-{uuid4().hex}",
                            before_data={"name": "before"},
                            base_entity_version=1,
                            form_data={"name": "after"},
                            change_items=[{"field": "name", "operation": "UPDATE"}],
                        ),
                        ApprovalApplication(
                            id=corrupt_approval_id,
                            business_type=BusinessType.MASTER_DATA_CHANGE,
                            business_id=str(corrupt_change_id),
                            applicant_id=str(applicant_id),
                            status=ApprovalStatus.DRAFT,
                            workflow_code=corrupt_workflow_code,
                            workflow_version=1,
                        ),
                        MasterDataChange(
                            id=corrupt_change_id,
                            approval_id=corrupt_approval_id,
                            entity_type="supplier",
                            entity_key=f"corrupt-{uuid4().hex}",
                            before_data={"name": "before"},
                            base_entity_version=1,
                            form_data={"name": "after"},
                            change_items=[{"field": "name", "operation": "UPDATE"}],
                        ),
                    ]
                )
                await session.commit()

            cases = (
                (missing_change_approval_id, ApprovalErrorCode.DATA_INTEGRITY_ERROR),
                (mismatch_approval_id, ApprovalErrorCode.DATA_INTEGRITY_ERROR),
                (unsupported_approval_id, ApprovalErrorCode.WORKFLOW_NOT_CONFIGURED),
                (corrupt_approval_id, ApprovalErrorCode.DATA_INTEGRITY_ERROR),
            )
            for approval_id, expected_code in cases:
                async with factory() as session:
                    with pytest.raises(ApprovalError) as error:
                        await ApprovalService(session).submit(approval_id, auth)
                    assert error.value.code == expected_code.value

            async with factory() as session:
                records = list(
                    (
                        await session.scalars(
                            select(ApprovalRecord).where(
                                ApprovalRecord.approval_id.in_(approval_ids),
                                ApprovalRecord.action == ApprovalAction.SUBMIT,
                            )
                        )
                    ).all()
                )
                assert records == []
                mismatch = await session.get(ApprovalApplication, mismatch_approval_id)
                assert mismatch is not None
                assert mismatch.status is ApprovalStatus.DRAFT
                assert mismatch.workflow_code is None
                assert mismatch.workflow_version is None
                assert mismatch.workflow_instance_id is None
                assert mismatch.current_task_id is None
                assert mismatch.current_node_code is None
                assert mismatch.submitted_at is None

                corrupt = await session.get(ApprovalApplication, corrupt_approval_id)
                assert corrupt is not None
                assert corrupt.status is ApprovalStatus.DRAFT
                assert corrupt.workflow_code == corrupt_workflow_code
                assert corrupt.workflow_version == 1
                assert corrupt.workflow_instance_id is None
                assert corrupt.current_task_id is None
                assert corrupt.current_node_code is None
                assert corrupt.submitted_at is None
        finally:
            async with factory() as session:
                await session.execute(
                    delete(ApprovalRecord).where(ApprovalRecord.approval_id.in_(approval_ids))
                )
                await session.execute(
                    delete(MasterDataChange).where(MasterDataChange.id.in_(change_ids))
                )
                await session.execute(
                    delete(ApprovalApplication).where(ApprovalApplication.id.in_(approval_ids))
                )
                await session.execute(
                    delete(WorkflowDefinition).where(
                        WorkflowDefinition.code == corrupt_workflow_code
                    )
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
