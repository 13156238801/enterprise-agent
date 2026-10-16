"""覆盖审批只读 API 的路由契约、权限和 PostgreSQL 查询行为。"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.deps import get_approval_service, get_auth_service
from app.approval.errors import ApprovalError, ApprovalErrorCode
from app.approval.repository import ApprovalRepository
from app.approval.service import ApprovalService
from app.auth.context import AuthContext
from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.infrastructure.database import create_db_engine, create_session_factory
from app.main import create_app
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange
from app.models.workflow import WorkflowDefinition
from app.workflow.repository import WorkflowDefinitionRepository


def _auth_context(roles: frozenset[UserRole], user_id: UUID | None = None) -> AuthContext:
    """构造查询 API 测试使用的可信身份上下文。"""
    return AuthContext(
        session_id=uuid4(),
        user_id=user_id or uuid4(),
        username="approval-reader",
        display_name="Approval Reader",
        roles=roles,
    )


def _client_for(auth_context: AuthContext, service: object) -> TestClient:
    """创建使用测试身份与服务替身的 FastAPI 客户端。"""

    class FakeAuthService:
        """为 HTTP 测试返回固定可信身份。"""

        async def authenticate(self, token: str) -> AuthContext:
            """验证测试 Bearer 值并返回当前测试用户。"""
            assert token == "test-token"
            return auth_context

    settings = Settings(_env_file=None)
    api = create_app(settings)
    api.dependency_overrides[get_auth_service] = lambda: FakeAuthService()
    api.dependency_overrides[get_approval_service] = lambda: service
    return TestClient(RequestIdMiddleware(api, settings))


def test_pending_route_is_registered_before_dynamic_approval_id_route() -> None:
    """验证 pending 命中待办处理器，而非动态 UUID 路径参数。"""
    auth = _auth_context(frozenset({UserRole.APPROVER}))

    class PendingService:
        """返回空待办页以区分静态路由是否被正确匹配。"""

        async def list_pending_approvals(self, received_auth, *, limit, offset):
            """返回待办接口的稳定空分页响应。"""
            assert received_auth is auth
            return {"items": [], "limit": limit, "offset": offset, "has_more": False}

    response = _client_for(auth, PendingService()).get(
        "/api/v1/approvals/pending", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 20, "offset": 0, "has_more": False}


@pytest.mark.parametrize("role", [UserRole.APPROVER, UserRole.DATA_ADMIN])
def test_pending_route_allows_each_explicit_reviewer_role(role: UserRole) -> None:
    """验证 APPROVER 与 DATA_ADMIN 均可通过 pending 路由门禁。"""
    auth = _auth_context(frozenset({role}))

    class PendingService:
        """记录通过 coarse role gate 后的 Service 调用。"""

        calls = 0

        async def list_pending_approvals(self, received_auth, *, limit, offset):
            """确认请求身份和默认分页值后返回空结果。"""
            assert received_auth is auth
            assert limit == 20 and offset == 0
            self.calls += 1
            return {"items": [], "limit": limit, "offset": offset, "has_more": False}

    service = PendingService()
    response = _client_for(auth, service).get(
        "/api/v1/approvals/pending", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200
    assert service.calls == 1


def test_my_approvals_route_uses_authenticated_identity_and_pagination() -> None:
    """验证我的审批路由从认证上下文调用 Service 并传递分页参数。"""
    auth = _auth_context(frozenset({UserRole.APPLICANT}))

    class ListService:
        """记录列表查询参数并返回空分页响应。"""

        async def list_my_approvals(self, received_auth, *, status, limit, offset):
            """返回分页响应并确认路由没有接受客户端身份。"""
            assert received_auth is auth
            assert status is None
            return {"items": [], "limit": limit, "offset": offset, "has_more": False}

    response = _client_for(auth, ListService()).get(
        "/api/v1/approvals?limit=7&offset=3",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 7, "offset": 3, "has_more": False}


def test_my_approvals_route_parses_status_enum_and_rejects_invalid_pagination() -> None:
    """验证 status 由 FastAPI 转为枚举且越界参数不会进入 Service。"""
    from app.domain.approval.enums import ApprovalStatus

    auth = _auth_context(frozenset({UserRole.APPLICANT}))

    class ListService:
        """验证路由传递解析后的状态和默认分页值。"""

        async def list_my_approvals(self, received_auth, *, status, limit, offset):
            """确认参数由 FastAPI 解析且调用身份来自依赖上下文。"""
            assert received_auth is auth
            assert status is ApprovalStatus.DRAFT
            assert limit == 20 and offset == 0
            return {"items": [], "limit": limit, "offset": offset, "has_more": False}

    response = _client_for(auth, ListService()).get(
        "/api/v1/approvals?status=DRAFT", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200

    class NeverCalledService:
        """确保无效分页在 HTTP 参数校验阶段被拒绝。"""

        async def list_my_approvals(self, *args, **kwargs):
            """拒绝不应发生的无效查询调用。"""
            raise AssertionError("invalid query must not call service")

    invalid = _client_for(auth, NeverCalledService()).get(
        "/api/v1/approvals?limit=101", headers={"Authorization": "Bearer test-token"}
    )
    assert invalid.status_code == 422
    assert "detail" in invalid.json()
    invalid_status = _client_for(auth, NeverCalledService()).get(
        "/api/v1/approvals?status=NOT_A_STATUS",
        headers={"Authorization": "Bearer test-token"},
    )
    assert invalid_status.status_code == 422
    assert "detail" in invalid_status.json()


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.APPLICANT])
def test_pending_route_rejects_non_reviewer_without_calling_service(role: UserRole) -> None:
    """验证 ADMIN-only 和 APPLICANT-only 均不能进入待办 Service。"""

    class NeverCalledService:
        """若权限门禁失效则使测试立即失败。"""

        async def list_pending_approvals(self, *args, **kwargs):
            """拒绝不应发生的待办查询调用。"""
            raise AssertionError("pending service must not be called")

    response = _client_for(_auth_context(frozenset({role})), NeverCalledService()).get(
        "/api/v1/approvals/pending", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"


def test_detail_and_history_routes_delegate_data_permission_to_service() -> None:
    """验证详情和历史路由只认证身份，数据读取权限由 Service 判定。"""
    auth = _auth_context(frozenset({UserRole.ADMIN}))

    class ForbiddenReadService:
        """记录读取调用并模拟 Service 的数据权限拒绝。"""

        calls = 0

        async def get_detail(self, approval_id, received_auth):
            """拒绝详情读取并记录已通过路由认证的调用。"""
            assert received_auth is auth
            self.calls += 1
            raise ApprovalError(ApprovalErrorCode.FORBIDDEN)

        async def get_history(self, approval_id, received_auth):
            """拒绝历史读取并记录已通过路由认证的调用。"""
            assert received_auth is auth
            self.calls += 1
            raise ApprovalError(ApprovalErrorCode.FORBIDDEN)

    service = ForbiddenReadService()
    client = _client_for(auth, service)
    headers = {"Authorization": "Bearer test-token", "X-Request-ID": "read-forbidden"}
    approval_id = uuid4()
    responses = (
        client.get(f"/api/v1/approvals/{approval_id}", headers=headers),
        client.get(f"/api/v1/approvals/{approval_id}/history", headers=headers),
    )
    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "APPROVAL_FORBIDDEN"
        assert response.json()["error"]["request_id"] == "read-forbidden"
    assert service.calls == 2


def test_read_integrity_error_uses_error_envelope_and_request_id() -> None:
    """验证只读 Service 完整性错误通过统一 Envelope 保留 request ID。"""
    auth = _auth_context(frozenset({UserRole.APPLICANT}))

    class BrokenReadService:
        """模拟读取数据损坏时 Service 的稳定业务错误。"""

        async def get_detail(self, approval_id, received_auth):
            """以审批完整性错误拒绝损坏详情。"""
            assert received_auth is auth
            raise ApprovalError(ApprovalErrorCode.DATA_INTEGRITY_ERROR)

    response = _client_for(auth, BrokenReadService()).get(
        f"/api/v1/approvals/{uuid4()}",
        headers={
            "Authorization": "Bearer test-token",
            "X-Request-ID": "approval-read-integrity",
        },
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "APPROVAL_DATA_INTEGRITY_ERROR"
    assert response.json()["error"]["request_id"] == "approval-read-integrity"


def test_all_approval_read_routes_require_bearer_authentication() -> None:
    """验证四个只读路由均要求 Bearer 身份并保留统一 request ID。"""

    class UnauthenticatedService:
        """使缺少凭据时不可能进入审批读取服务。"""

        async def authenticate(self, token: str) -> AuthContext:
            """拒绝未提供的 Bearer 身份。"""
            raise AssertionError(f"unexpected token: {token}")

    api = create_app(Settings(_env_file=None))
    api.dependency_overrides[get_auth_service] = lambda: UnauthenticatedService()
    client = TestClient(RequestIdMiddleware(api, Settings(_env_file=None)))
    paths = (
        "/api/v1/approvals",
        "/api/v1/approvals/pending",
        f"/api/v1/approvals/{uuid4()}",
        f"/api/v1/approvals/{uuid4()}/history",
    )
    for path in paths:
        response = client.get(path, headers={"X-Request-ID": "approval-read-auth"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
        assert response.json()["error"]["request_id"] == "approval-read-auth"


def test_postgres_approval_read_permissions_pagination_and_integrity(monkeypatch) -> None:
    """在 PostgreSQL 验证列表、待办、详情、周期权限和安全历史读取。"""
    from app.core.config import get_settings
    from app.domain.workflow.definition import workflow_definition_to_dict

    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    workflow_code = f"read_{uuid4().hex}"
    applicant_id, other_applicant_id, reviewer_id = uuid4(), uuid4(), uuid4()
    data_admin_id, other_reviewer_id = uuid4(), uuid4()
    approval_ids: list[UUID] = []
    v1 = WorkflowDefinitionSpec(
        workflow_code,
        1,
        (
            WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
            WorkflowNodeDefinition("data_admin_review", UserRole.DATA_ADMIN),
        ),
    )
    v2 = WorkflowDefinitionSpec(
        workflow_code,
        2,
        (
            WorkflowNodeDefinition("manager_review", UserRole.DATA_ADMIN),
            WorkflowNodeDefinition("data_admin_review", UserRole.APPROVER),
        ),
    )
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    async def add_case(
        session,
        *,
        approval_key: UUID | None = None,
        owner: UUID = applicant_id,
        status: ApprovalStatus = ApprovalStatus.IN_APPROVAL,
        node: str | None = "manager_review",
        workflow_version: int | None = 1,
        workflow_instance: str | None = "instance",
        task_id: str | None = None,
        target_operator: str | None = None,
        task_owner: UUID | None = None,
        submitted_at: datetime | None = None,
        created_at: datetime | None = None,
        include_change: bool = True,
        business_id: str | None = None,
        task_history: bool = True,
        history: list[tuple[ApprovalAction, str, datetime]] | None = None,
        workflow_code_override: str | None = None,
    ) -> UUID:
        """写入一条申请及可选变更、task 创建记录和指定周期历史。"""
        approval_id, change_id = approval_key or uuid4(), uuid4()
        approval_ids.append(approval_id)
        current_task = task_id or (str(uuid4()) if node is not None else None)
        created = created_at or base_time
        application = ApprovalApplication(
            id=approval_id,
            business_type=BusinessType.MASTER_DATA_CHANGE,
            business_id=business_id or str(change_id),
            applicant_id=str(owner),
            status=status,
            workflow_code=(
                workflow_code_override
                if workflow_code_override is not None
                else workflow_code
                if workflow_version is not None
                else None
            ),
            workflow_version=workflow_version,
            workflow_instance_id=workflow_instance,
            current_task_id=current_task,
            current_node_code=node,
            current_node_name=None,
            submitted_at=submitted_at
            or (created if status is ApprovalStatus.IN_APPROVAL else None),
            created_at=created,
            updated_at=created,
        )
        session.add(application)
        if include_change:
            session.add(
                MasterDataChange(
                    id=change_id,
                    approval_id=approval_id,
                    entity_type="supplier",
                    entity_key=f"supplier-{uuid4().hex}",
                    before_data={"currency": "CNY"},
                    base_entity_version=1,
                    form_data={"currency": "USD"},
                    change_items=[
                        {
                            "field": "currency",
                            "operation": "UPDATE",
                            "before": "CNY",
                            "after": "USD",
                        }
                    ],
                    change_reason="currency update",
                    created_at=created,
                    updated_at=created,
                )
            )
        if task_history and current_task is not None:
            session.add(
                ApprovalRecord(
                    approval_id=approval_id,
                    operator_id=str(task_owner or owner),
                    action=ApprovalAction.SUBMIT,
                    from_status=ApprovalStatus.DRAFT,
                    to_status=ApprovalStatus.IN_APPROVAL,
                    target_task_id=current_task,
                    target_node_code=node,
                    target_operator_id=target_operator,
                    created_at=created,
                )
            )
        for action, operator_id, event_time in history or []:
            session.add(
                ApprovalRecord(
                    approval_id=approval_id,
                    operator_id=operator_id,
                    action=action,
                    from_status=(
                        ApprovalStatus.DRAFT
                        if action in {ApprovalAction.CREATE, ApprovalAction.SAVE}
                        else ApprovalStatus.IN_APPROVAL
                    ),
                    to_status=(
                        status
                        if status in {ApprovalStatus.DRAFT, ApprovalStatus.COMPLETED}
                        else ApprovalStatus.IN_APPROVAL
                    ),
                    from_node_code="manager_review",
                    target_task_id=current_task if node == "data_admin_review" else None,
                    target_node_code=node,
                    created_at=event_time,
                )
            )
        await session.flush()
        return approval_id

    async def run() -> None:
        """运行真实数据库读场景并在 finally 中清除随机数据。"""
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
                tie_id_a = uuid4()
                tie_id_b = UUID(int=tie_id_a.int + 1)
                pending_tie_id_a = uuid4()
                pending_tie_id_b = UUID(int=pending_tie_id_a.int + 1)
                initial_draft = await add_case(
                    session,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                    submitted_at=None,
                    created_at=base_time + timedelta(days=3),
                    history=[],
                )
                list_tie_a = await add_case(
                    session,
                    approval_key=tie_id_a,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_instance="list-tie-a",
                    submitted_at=base_time,
                    created_at=base_time + timedelta(days=5),
                    history=[],
                )
                list_tie_b = await add_case(
                    session,
                    approval_key=tie_id_b,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_instance="list-tie-b",
                    submitted_at=base_time,
                    created_at=base_time + timedelta(days=5),
                    history=[],
                )
                retained_draft = await add_case(
                    session,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_version=1,
                    workflow_instance="old-instance",
                    submitted_at=None,
                    created_at=base_time + timedelta(days=2),
                    history=[
                        (ApprovalAction.WITHDRAW, str(applicant_id), base_time + timedelta(days=2))
                    ],
                )
                session.add_all(
                    [
                        ApprovalRecord(
                            approval_id=retained_draft,
                            operator_id=str(applicant_id),
                            action=ApprovalAction.CREATE,
                            to_status=ApprovalStatus.DRAFT,
                            created_at=base_time + timedelta(days=2) - timedelta(minutes=2),
                        ),
                        ApprovalRecord(
                            approval_id=retained_draft,
                            operator_id=str(applicant_id),
                            action=ApprovalAction.SAVE,
                            from_status=ApprovalStatus.DRAFT,
                            to_status=ApprovalStatus.DRAFT,
                            created_at=base_time + timedelta(days=2) - timedelta(minutes=1),
                        ),
                    ]
                )
                owner_in_approval = await add_case(
                    session,
                    status=ApprovalStatus.IN_APPROVAL,
                    node="manager_review",
                    created_at=base_time + timedelta(days=1),
                    submitted_at=base_time + timedelta(days=1),
                )
                await add_case(
                    session,
                    owner=other_applicant_id,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                    submitted_at=None,
                    created_at=base_time + timedelta(days=4),
                    history=[],
                )
                role_pool_old = await add_case(
                    session,
                    created_at=base_time,
                    submitted_at=base_time,
                )
                role_pool_new = await add_case(
                    session,
                    created_at=base_time + timedelta(hours=1),
                    submitted_at=base_time + timedelta(hours=1),
                )
                data_admin_role_pool = await add_case(
                    session,
                    node="data_admin_review",
                    created_at=base_time + timedelta(hours=1, minutes=30),
                    submitted_at=base_time + timedelta(hours=1, minutes=30),
                )
                targeted = await add_case(
                    session,
                    target_operator=str(reviewer_id),
                    created_at=base_time + timedelta(hours=2),
                    submitted_at=base_time + timedelta(hours=2),
                )
                tie_pending_a = await add_case(
                    session,
                    approval_key=pending_tie_id_a,
                    created_at=base_time + timedelta(hours=14),
                    submitted_at=base_time + timedelta(hours=14),
                )
                tie_pending_b = await add_case(
                    session,
                    approval_key=pending_tie_id_b,
                    created_at=base_time + timedelta(hours=14),
                    submitted_at=base_time + timedelta(hours=14),
                )
                wrong_target = await add_case(
                    session,
                    target_operator=str(other_reviewer_id),
                    created_at=base_time - timedelta(hours=1),
                    submitted_at=base_time - timedelta(hours=1),
                )
                wrong_role = await add_case(
                    session,
                    target_operator=str(reviewer_id),
                    node="data_admin_review",
                    created_at=base_time + timedelta(hours=4),
                    submitted_at=base_time + timedelta(hours=4),
                )
                participant = await add_case(
                    session,
                    owner=other_applicant_id,
                    node="data_admin_review",
                    task_owner=reviewer_id,
                    created_at=base_time + timedelta(hours=5),
                    submitted_at=base_time + timedelta(hours=5),
                    history=[
                        (ApprovalAction.APPROVE, str(reviewer_id), base_time + timedelta(hours=6))
                    ],
                )
                previous_cycle = await add_case(
                    session,
                    owner=other_applicant_id,
                    node="manager_review",
                    target_operator=str(other_reviewer_id),
                    created_at=base_time + timedelta(hours=7),
                    submitted_at=base_time + timedelta(hours=9),
                    history=[
                        (ApprovalAction.APPROVE, str(reviewer_id), base_time + timedelta(hours=8)),
                        (
                            ApprovalAction.SUBMIT,
                            str(other_applicant_id),
                            base_time + timedelta(hours=9),
                        ),
                    ],
                )
                returned_draft = await add_case(
                    session,
                    owner=other_applicant_id,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_instance="returned-instance",
                    submitted_at=None,
                    history=[
                        (ApprovalAction.REJECT, str(reviewer_id), base_time + timedelta(hours=10))
                    ],
                )
                withdrawn_draft = await add_case(
                    session,
                    owner=other_applicant_id,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_instance="withdrawn-instance",
                    submitted_at=None,
                    history=[
                        (ApprovalAction.WITHDRAW, str(reviewer_id), base_time + timedelta(hours=11))
                    ],
                )
                completed_participant = await add_case(
                    session,
                    owner=other_applicant_id,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_version=1,
                    workflow_instance="completed-instance",
                    task_history=False,
                    submitted_at=base_time,
                    history=[
                        (ApprovalAction.SUBMIT, str(other_applicant_id), base_time),
                        (
                            ApprovalAction.APPROVE,
                            str(reviewer_id),
                            base_time + timedelta(minutes=1),
                        ),
                    ],
                )
                same_time_after = await add_case(
                    session,
                    owner=other_applicant_id,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_instance="same-time-after",
                    task_history=False,
                    submitted_at=base_time,
                    history=[],
                )
                same_time_before = await add_case(
                    session,
                    owner=other_applicant_id,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_instance="same-time-before",
                    task_history=False,
                    submitted_at=base_time,
                    history=[],
                )
                boundary_after_submit_id = UUID(int=2**128 - 10)
                boundary_after_action_id = UUID(int=2**128 - 9)
                boundary_before_submit_id = UUID(int=2**128 - 3)
                boundary_before_action_id = UUID(int=2**128 - 4)
                session.add_all(
                    [
                        ApprovalRecord(
                            id=boundary_after_submit_id,
                            approval_id=same_time_after,
                            operator_id=str(other_applicant_id),
                            action=ApprovalAction.SUBMIT,
                            from_status=ApprovalStatus.DRAFT,
                            to_status=ApprovalStatus.IN_APPROVAL,
                            created_at=base_time,
                        ),
                        ApprovalRecord(
                            id=boundary_after_action_id,
                            approval_id=same_time_after,
                            operator_id=str(reviewer_id),
                            action=ApprovalAction.APPROVE,
                            from_status=ApprovalStatus.IN_APPROVAL,
                            to_status=ApprovalStatus.COMPLETED,
                            created_at=base_time,
                        ),
                        ApprovalRecord(
                            id=boundary_before_submit_id,
                            approval_id=same_time_before,
                            operator_id=str(other_applicant_id),
                            action=ApprovalAction.SUBMIT,
                            from_status=ApprovalStatus.DRAFT,
                            to_status=ApprovalStatus.IN_APPROVAL,
                            created_at=base_time,
                        ),
                        ApprovalRecord(
                            id=boundary_before_action_id,
                            approval_id=same_time_before,
                            operator_id=str(reviewer_id),
                            action=ApprovalAction.APPROVE,
                            from_status=ApprovalStatus.IN_APPROVAL,
                            to_status=ApprovalStatus.COMPLETED,
                            created_at=base_time,
                        ),
                    ]
                )
                await session.commit()

            owner_auth = _auth_context(frozenset({UserRole.APPLICANT}), applicant_id)
            reviewer_auth = _auth_context(frozenset({UserRole.APPROVER}), reviewer_id)
            other_reviewer_auth = _auth_context(frozenset({UserRole.APPROVER}), other_reviewer_id)
            data_admin_auth = _auth_context(frozenset({UserRole.DATA_ADMIN}), data_admin_id)
            admin_auth = _auth_context(frozenset({UserRole.ADMIN}), uuid4())

            async with factory() as session:
                service = ApprovalService(session)
                mine = await service.list_my_approvals(owner_auth, status=None, limit=2, offset=0)
                assert len(mine.items) == 2 and mine.has_more
                assert [item.approval_id for item in mine.items] == [list_tie_b, list_tie_a]
                assert all(item.applicant_id == str(applicant_id) for item in mine.items)
                assert not any(item.approval_id == role_pool_old for item in mine.items)
                assert all("current_task_id" not in item.model_dump() for item in mine.items)
                draft_page = await service.list_my_approvals(
                    owner_auth, status=ApprovalStatus.DRAFT, limit=100, offset=0
                )
                assert {item.approval_id for item in draft_page.items} == {
                    retained_draft,
                    initial_draft,
                }
                assert all(item.current_node_role is None for item in draft_page.items)
                assert (
                    await service.list_my_approvals(owner_auth, status=None, limit=2, offset=100)
                ).has_more is False
                all_mine_in_approval = await service.list_my_approvals(
                    owner_auth,
                    status=ApprovalStatus.IN_APPROVAL,
                    limit=100,
                    offset=0,
                )
                current_summary = next(
                    item
                    for item in all_mine_in_approval.items
                    if item.approval_id == owner_in_approval
                )
                assert current_summary.current_node_role is UserRole.APPROVER
                completed_page = await service.list_my_approvals(
                    owner_auth, status=ApprovalStatus.COMPLETED, limit=100, offset=0
                )
                assert [item.approval_id for item in completed_page.items] == [
                    list_tie_b,
                    list_tie_a,
                ]

            async with factory() as session:
                service = ApprovalService(session)
                pending = await service.list_pending_approvals(reviewer_auth, limit=1, offset=0)
                assert len(pending.items) == 1 and pending.has_more
                assert pending.items[0].approval_id == role_pool_old
                assert pending.items[0].current_node_role is UserRole.APPROVER
                second_page = await service.list_pending_approvals(reviewer_auth, limit=1, offset=1)
                assert second_page.items[0].approval_id == role_pool_new
                targeted_page = await service.list_pending_approvals(
                    reviewer_auth, limit=20, offset=0
                )
                visible_ids = {item.approval_id for item in targeted_page.items}
                assert targeted in visible_ids
                assert wrong_target not in visible_ids
                assert wrong_role not in visible_ids
                assert participant not in visible_ids
                assert all("target_operator_id" not in item.model_dump() for item in pending.items)
                data_admin_pending = await service.list_pending_approvals(
                    data_admin_auth, limit=20, offset=0
                )
                assert all(item.approval_id != role_pool_old for item in data_admin_pending.items)
                assert data_admin_role_pool in {
                    item.approval_id for item in data_admin_pending.items
                }
                assert {item.approval_id for item in targeted_page.items} == {
                    role_pool_old,
                    role_pool_new,
                    targeted,
                    tie_pending_a,
                    tie_pending_b,
                    owner_in_approval,
                }
                ordered_ids = [item.approval_id for item in targeted_page.items]
                assert ordered_ids.index(tie_pending_a) < ordered_ids.index(tie_pending_b)
                final_eligible_page = await service.list_pending_approvals(
                    reviewer_auth, limit=3, offset=3
                )
                assert len(final_eligible_page.items) == 3
                assert not final_eligible_page.has_more

            async with factory() as session:
                service = ApprovalService(session)
                owner_detail = await service.get_detail(owner_in_approval, owner_auth)
                assert owner_detail.change.before_data == {"currency": "CNY"}
                assert owner_detail.change.form_data == {"currency": "USD"}
                assert owner_detail.change.change_reason == "currency update"
                assert owner_detail.current_node_role is UserRole.APPROVER
                rendered = owner_detail.model_dump(mode="json")
                assert "current_task_id" not in rendered
                assert "target_operator_id" not in rendered
                assert "workflow JSON" not in rendered
                reviewer_detail = await service.get_detail(owner_in_approval, reviewer_auth)
                assert reviewer_detail.approval_id == owner_in_approval
                initial_detail = await service.get_detail(initial_draft, owner_auth)
                assert initial_detail.status is ApprovalStatus.DRAFT
                other_applicant_auth = _auth_context(
                    frozenset({UserRole.APPLICANT}), other_applicant_id
                )
                assert (
                    await service.get_detail(completed_participant, other_applicant_auth)
                ).status is ApprovalStatus.COMPLETED
                assert (
                    await service.get_detail(role_pool_old, reviewer_auth)
                ).approval_id == role_pool_old
                assert (await service.get_detail(targeted, reviewer_auth)).approval_id == targeted
                prior_participant = await service.get_detail(participant, reviewer_auth)
                assert prior_participant.approval_id == participant
                completed_detail = await service.get_detail(completed_participant, reviewer_auth)
                assert completed_detail.status is ApprovalStatus.COMPLETED
                assert (
                    await service.get_detail(same_time_after, reviewer_auth)
                ).approval_id == same_time_after
                with pytest.raises(ApprovalError) as error:
                    await service.get_detail(same_time_before, reviewer_auth)
                assert error.value.code == ApprovalErrorCode.FORBIDDEN.value
                for approval_id, auth in (
                    (targeted, other_reviewer_auth),
                    (wrong_role, reviewer_auth),
                    (participant, admin_auth),
                    (previous_cycle, reviewer_auth),
                    (returned_draft, reviewer_auth),
                    (withdrawn_draft, reviewer_auth),
                    (initial_draft, reviewer_auth),
                    (owner_in_approval, other_applicant_auth),
                ):
                    with pytest.raises(ApprovalError) as error:
                        await service.get_detail(approval_id, auth)
                    assert error.value.code == ApprovalErrorCode.FORBIDDEN.value
                with pytest.raises(ApprovalError) as error:
                    await service.get_detail(uuid4(), owner_auth)
                assert error.value.code == ApprovalErrorCode.NOT_FOUND.value
                history = await service.get_history(owner_in_approval, owner_auth)
                assert [item.action for item in history.items] == [ApprovalAction.SUBMIT]
                assert history.items[0].from_status is ApprovalStatus.DRAFT
                assert history.items[0].to_status is ApprovalStatus.IN_APPROVAL
                assert history.items[0].target_node_code == "manager_review"
                history_payload = history.model_dump(mode="json")
                assert "from_task_id" not in str(history_payload)
                assert "target_task_id" not in str(history_payload)
                assert "target_operator_id" not in str(history_payload)
                retained_history = await service.get_history(retained_draft, owner_auth)
                assert [item.action.value for item in retained_history.items] == [
                    "CREATE",
                    "SAVE",
                    "WITHDRAW",
                ]
                participant_history = await service.get_history(participant, reviewer_auth)
                assert [item.action for item in participant_history.items] == [
                    ApprovalAction.SUBMIT,
                    ApprovalAction.APPROVE,
                ]
                with pytest.raises(ApprovalError) as error:
                    await service.get_history(targeted, other_reviewer_auth)
                assert error.value.code == ApprovalErrorCode.FORBIDDEN.value
                assert not (await service.get_history(initial_draft, owner_auth)).items

            async def assert_pending_integrity_error() -> None:
                """确认任一损坏审批中候选都会使待办查询报告完整性错误。"""
                async with factory() as session:
                    with pytest.raises(ApprovalError) as error:
                        await ApprovalService(session).list_pending_approvals(
                            reviewer_auth, limit=20, offset=0
                        )
                    assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value

            for field_name in (
                "workflow_instance_id",
                "current_task_id",
                "current_node_code",
            ):
                async with factory() as session:
                    application = await session.get(ApprovalApplication, role_pool_new)
                    assert application is not None
                    original_value = getattr(application, field_name)
                    setattr(application, field_name, None)
                    await session.commit()
                await assert_pending_integrity_error()
                async with factory() as session:
                    application = await session.get(ApprovalApplication, role_pool_new)
                    assert application is not None
                    setattr(application, field_name, original_value)
                    await session.commit()

            async with factory() as session:
                application = await session.get(ApprovalApplication, role_pool_new)
                assert application is not None
                original_binding = (application.workflow_code, application.workflow_version)
                application.workflow_code = None
                application.workflow_version = None
                await session.commit()
            await assert_pending_integrity_error()
            async with factory() as session:
                application = await session.get(ApprovalApplication, role_pool_new)
                assert application is not None
                application.workflow_code, application.workflow_version = original_binding
                await session.commit()

            async with factory() as session:
                application = await session.get(ApprovalApplication, role_pool_new)
                assert application is not None
                original_node = application.current_node_code
                application.current_node_code = "unknown_node"
                await session.commit()
            await assert_pending_integrity_error()
            async with factory() as session:
                application = await session.get(ApprovalApplication, role_pool_new)
                assert application is not None
                application.current_node_code = original_node
                await session.commit()

            async with factory() as session:
                task_record = await ApprovalRepository(session).get_task_creation_record(
                    (await session.get(ApprovalApplication, role_pool_new)).current_task_id
                )
                assert task_record is not None
                original_task_owner = task_record.approval_id
                task_record.approval_id = owner_in_approval
                await session.commit()
            await assert_pending_integrity_error()
            async with factory() as session:
                task_record = await session.get(ApprovalRecord, task_record.id)
                assert task_record is not None
                task_record.approval_id = original_task_owner
                await session.commit()

            async with factory() as session:
                application = await session.get(ApprovalApplication, role_pool_new)
                assert application is not None and application.current_task_id is not None
                task_record = await ApprovalRepository(session).get_task_creation_record(
                    application.current_task_id
                )
                assert task_record is not None
                task_record_id = task_record.id
                await session.delete(task_record)
                await session.commit()
            await assert_pending_integrity_error()
            async with factory() as session:
                session.add(
                    ApprovalRecord(
                        id=task_record_id,
                        approval_id=role_pool_new,
                        operator_id=str(applicant_id),
                        action=ApprovalAction.SUBMIT,
                        from_status=ApprovalStatus.DRAFT,
                        to_status=ApprovalStatus.IN_APPROVAL,
                        target_task_id=(
                            await session.get(ApprovalApplication, role_pool_new)
                        ).current_task_id,
                        target_node_code="manager_review",
                        created_at=base_time,
                    )
                )
                await session.commit()

            async def missing_bound_workflow(repository, code: str, version: int):
                """模拟精确绑定流程不可读取，确认不回退到 active 版本。"""
                return None

            monkeypatch.setattr(
                WorkflowDefinitionRepository,
                "get_by_code_version",
                missing_bound_workflow,
            )
            await assert_pending_integrity_error()
            monkeypatch.undo()

            # 坏的待办候选必须触发完整性错误，不能从结果中静默省略。
            async with factory() as session:
                session.add(
                    WorkflowDefinition(
                        code=workflow_code,
                        version=3,
                        definition_json={"invalid": True},
                        active=False,
                    )
                )
                await session.flush()
                await add_case(
                    session,
                    task_history=False,
                    created_at=base_time + timedelta(hours=10),
                    submitted_at=base_time + timedelta(hours=10),
                )
                corrupt = await add_case(
                    session,
                    node="manager_review",
                    workflow_version=3,
                    created_at=base_time + timedelta(hours=12),
                )
                unknown_node = await add_case(
                    session,
                    node="unknown_node",
                    created_at=base_time + timedelta(hours=12, minutes=30),
                    submitted_at=base_time + timedelta(hours=12, minutes=30),
                )
                no_history_approval = await add_case(
                    session,
                    status=ApprovalStatus.COMPLETED,
                    node=None,
                    workflow_instance="empty-history-completed",
                    task_history=False,
                    created_at=base_time + timedelta(hours=11),
                    submitted_at=base_time + timedelta(hours=11),
                )
                await session.commit()
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).list_pending_approvals(
                        reviewer_auth, limit=20, offset=0
                    )
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).get_detail(corrupt, owner_auth)
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).get_detail(unknown_node, owner_auth)
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
            async with factory() as session:
                with pytest.raises(ApprovalError) as error:
                    await ApprovalService(session).get_history(no_history_approval, owner_auth)
                assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value

            async with factory() as session:
                missing_change = await add_case(
                    session,
                    include_change=False,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                    submitted_at=None,
                    history=[],
                )
                mismatched_link = await add_case(
                    session,
                    status=ApprovalStatus.DRAFT,
                    node=None,
                    workflow_version=None,
                    workflow_instance=None,
                    submitted_at=None,
                    business_id=str(uuid4()),
                    history=[],
                )
                await session.commit()
            async with factory() as session:
                service = ApprovalService(session)
                for approval_id in (missing_change, mismatched_link):
                    with pytest.raises(ApprovalError) as error:
                        await service.get_detail(approval_id, owner_auth)
                    assert error.value.code == ApprovalErrorCode.DATA_INTEGRITY_ERROR.value
        finally:
            async with factory() as session:
                if approval_ids:
                    await session.execute(
                        delete(ApprovalRecord).where(ApprovalRecord.approval_id.in_(approval_ids))
                    )
                    await session.execute(
                        delete(MasterDataChange).where(
                            MasterDataChange.approval_id.in_(approval_ids)
                        )
                    )
                    await session.execute(
                        delete(ApprovalApplication).where(ApprovalApplication.id.in_(approval_ids))
                    )
                await session.execute(
                    delete(WorkflowDefinition).where(WorkflowDefinition.code == workflow_code)
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
