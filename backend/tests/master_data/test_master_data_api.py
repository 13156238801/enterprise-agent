"""主数据 HTTP API 的认证、授权和错误 envelope 测试。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps import get_current_auth, get_master_data_service
from app.auth.context import AuthContext
from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.domain.auth.enums import UserRole
from app.main import create_app
from app.master_data.errors import MasterDataError, MasterDataErrorCode
from app.master_data.service import MasterDataService


def _auth_context(role: UserRole) -> AuthContext:
    """构造仅供 API 测试使用的服务端可信身份。"""
    return AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        username="api-test",
        display_name="API Test",
        roles=frozenset({role}),
    )


def test_authenticated_get_returns_entity_without_sensitive_fields() -> None:
    """验证任一已认证身份可读主数据且响应不包含认证秘密。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.ADMIN)

    class Service:
        """提供只读 API 测试所需的实体响应。"""

        async def get_entity(self, entity_type: str, entity_key: str) -> dict[str, object]:
            """返回路由测试用的公开实体字段。"""
            assert (entity_type, entity_key) == ("supplier", "supplier_a")
            return {
                "id": str(uuid4()),
                "entity_type": entity_type,
                "entity_key": entity_key,
                "data": {"name": "Demo Supplier A"},
                "version": 1,
            }

    app.dependency_overrides[get_master_data_service] = lambda: Service()
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.get("/api/v1/master-data/supplier/supplier_a")

    assert response.status_code == 200
    assert response.json()["entity_key"] == "supplier_a"
    assert not {"password", "password_hash", "token_hash", "access_token"} & set(response.json())


def test_applicant_can_create_draft_and_error_has_request_id() -> None:
    """验证 APPLICANT 可发起草稿且应用错误进入带 Request ID 的 Envelope。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.APPLICANT)

    class Service:
        """为错误映射测试注入稳定的主数据业务异常。"""

        async def create_or_update_draft(self, *args, **kwargs):
            """模拟 Service 以稳定业务错误拒绝空变化。"""
            raise MasterDataError(MasterDataErrorCode.NO_CHANGE)

    app.dependency_overrides[get_master_data_service] = lambda: Service()
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.post(
            "/api/v1/master-data/supplier/supplier_a/changes",
            json={"changes": {"enabled": True}, "reason": "no-op"},
            headers={"X-Request-ID": "master-data-error"},
        )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "master-data-error"
    assert response.json()["error"]["code"] == "MASTER_DATA_NO_CHANGE"
    assert response.json()["error"]["request_id"] == "master-data-error"


def test_applicant_draft_response_contains_structured_diff() -> None:
    """验证 APPLICANT 草稿 API 返回完整快照和结构化 Diff。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.APPLICANT)

    class Service:
        """返回创建草稿 API 测试使用的结构化结果。"""

        async def create_or_update_draft(self, *args, **kwargs):
            """返回模拟 Service 生成的结构化草稿响应。"""
            return {
                "approval_id": str(uuid4()),
                "change_id": str(uuid4()),
                "status": "DRAFT",
                "entity_type": "supplier",
                "entity_key": "supplier_a",
                "base_entity_version": 1,
                "before": {"payment_terms": "PREPAID"},
                "after": {"payment_terms": "NET_30"},
                "diff": [
                    {
                        "field": "payment_terms",
                        "operation": "UPDATE",
                        "before": "PREPAID",
                        "after": "NET_30",
                    }
                ],
                "reason": "更新账期",
            }

    app.dependency_overrides[get_master_data_service] = lambda: Service()
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.post(
            "/api/v1/master-data/supplier/supplier_a/changes",
            json={"changes": {"payment_terms": "NET_30"}, "reason": "更新账期"},
        )

    assert response.status_code == 200
    assert response.json()["before"] == {"payment_terms": "PREPAID"}
    assert response.json()["diff"] == [
        {
            "field": "payment_terms",
            "operation": "UPDATE",
            "before": "PREPAID",
            "after": "NET_30",
        }
    ]


def test_approver_and_admin_cannot_create_applicant_drafts() -> None:
    """验证只有 APPLICANT 角色可以创建草稿，ADMIN 不具有隐式绕过权。"""
    settings = Settings(_env_file=None)
    for role in (UserRole.APPROVER, UserRole.ADMIN):
        app = create_app(settings)
        app.dependency_overrides[get_current_auth] = lambda role=role: _auth_context(role)

        class Service:
            """阻止无 APPLICANT 权限的请求调用草稿 Service。"""

            async def create_or_update_draft(self, *args, **kwargs):
                """记录不应被调用的受保护写操作。"""
                raise AssertionError("unauthorized role reached service")

        app.dependency_overrides[get_master_data_service] = lambda: Service()
        with TestClient(RequestIdMiddleware(app, settings)) as client:
            response = client.post(
                "/api/v1/master-data/supplier/supplier_a/changes",
                json={"changes": {"enabled": False}, "reason": "test"},
            )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"


def test_draft_request_rejects_client_supplied_identity() -> None:
    """验证请求体不能指定可信申请人或其他额外身份字段。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.APPLICANT)
    app.dependency_overrides[get_master_data_service] = lambda: object()
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.post(
            "/api/v1/master-data/supplier/supplier_a/changes",
            json={
                "changes": {"enabled": False},
                "reason": "test",
                "applicant_id": "forged-user",
            },
        )

    assert response.status_code == 422


def test_invalid_changes_use_master_data_error_envelope() -> None:
    """验证无效 changes 经主数据应用错误返回统一 Envelope 和 Request ID。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.APPLICANT)
    app.dependency_overrides[get_master_data_service] = lambda: MasterDataService(None)
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.post(
            "/api/v1/master-data/supplier/supplier_a/changes",
            json={"changes": [], "reason": "test"},
            headers={"X-Request-ID": "master-data-invalid"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MASTER_DATA_INVALID_CHANGE"
    assert response.json()["error"]["request_id"] == "master-data-invalid"


def test_invalid_reason_uses_master_data_error_envelope() -> None:
    """验证空白 reason 经主数据应用错误返回统一 Envelope 和 Request ID。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.APPLICANT)
    app.dependency_overrides[get_master_data_service] = lambda: MasterDataService(None)
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.post(
            "/api/v1/master-data/supplier/supplier_a/changes",
            json={"changes": {"currency": "USD"}, "reason": "  "},
            headers={"X-Request-ID": "master-data-invalid"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MASTER_DATA_INVALID_CHANGE"
    assert response.json()["error"]["request_id"] == "master-data-invalid"


def test_invalid_change_variants_use_master_data_error_envelope() -> None:
    """验证非 JSON changes、空 patch 和超长 reason 都使用统一业务错误。"""
    settings = Settings(_env_file=None)
    invalid_payloads = (
        '{"changes":{"value":NaN},"reason":"test"}',
        '{"changes":{},"reason":"test"}',
        '{"changes":{"value":"x"},"reason":"' + ("x" * 2001) + '"}',
    )
    for body in invalid_payloads:
        app = create_app(settings)
        app.dependency_overrides[get_current_auth] = lambda: _auth_context(UserRole.APPLICANT)
        app.dependency_overrides[get_master_data_service] = lambda: MasterDataService(None)
        with TestClient(RequestIdMiddleware(app, settings)) as client:
            response = client.post(
                "/api/v1/master-data/supplier/supplier_a/changes",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Request-ID": "master-data-invalid",
                },
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "MASTER_DATA_INVALID_CHANGE"
        assert response.json()["error"]["request_id"] == "master-data-invalid"


def test_unauthenticated_get_returns_bearer_challenge() -> None:
    """验证未认证主数据读取返回 AUTH_UNAUTHENTICATED 和 Bearer challenge。"""
    settings = Settings(_env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_master_data_service] = lambda: object()
    with TestClient(RequestIdMiddleware(app, settings)) as client:
        response = client.get("/api/v1/master-data/supplier/supplier_a")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"] == "Bearer"
