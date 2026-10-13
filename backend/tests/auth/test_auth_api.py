"""认证 HTTP API 行为测试。"""

from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_current_auth, get_db_session, require_any_role
from app.auth.context import AuthContext
from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.domain.auth.enums import UserRole
from app.main import create_app


def test_missing_bearer_returns_envelope_with_request_id() -> None:
    """验证缺失 Bearer Token 时返回统一 401 和当前 Request ID。"""
    app_settings = Settings()
    app = create_app(app_settings)

    async def unused_session():
        """提供无数据库的身份缺失测试依赖。"""
        yield None

    app.dependency_overrides[get_db_session] = unused_session
    client = TestClient(RequestIdMiddleware(app, app_settings), raise_server_exceptions=False)

    response = client.get("/api/v1/auth/me", headers={"X-Request-ID": "auth-test-1"})
    forged_identity = client.post(
        "/api/v1/auth/login",
        json={
            "username": "applicant",
            "password": "demo-password",
            "user_id": "forged-id",
            "role": "ADMIN",
        },
    )
    malformed = client.get("/api/v1/auth/me", headers={"Authorization": "Basic malformed"})

    assert forged_identity.status_code == 422
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["x-request-id"] == "auth-test-1"
    assert response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
    assert response.json()["error"]["request_id"] == "auth-test-1"


def test_role_guard_uses_or_semantics_without_admin_bypass() -> None:
    """验证角色依赖按 OR 语义授权且 ADMIN 不自动满足业务角色。"""
    app_settings = Settings()
    app = create_app(app_settings)
    identities = {
        "current": AuthContext(
            session_id=uuid4(),
            user_id=uuid4(),
            username="role-test",
            display_name="Role Test",
            roles=frozenset({UserRole.APPLICANT, UserRole.DATA_ADMIN}),
        )
    }

    async def current_auth() -> AuthContext:
        """为角色依赖测试提供服务端构造的身份对象。"""
        return identities["current"]

    async def guarded_endpoint(
        auth_context: AuthContext = Depends(
            require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)
        ),
    ) -> dict[str, str]:
        """返回角色授权后的非敏感身份字段。"""
        return {"username": auth_context.username}

    app.dependency_overrides[get_current_auth] = current_auth
    app.add_api_route("/test/role", guarded_endpoint)
    client = TestClient(RequestIdMiddleware(app, app_settings), raise_server_exceptions=False)

    allowed = client.get("/test/role", headers={"X-Request-ID": "role-allowed"})
    identities["current"] = AuthContext(
        session_id=uuid4(),
        user_id=uuid4(),
        username="admin-only",
        display_name="Demo Admin",
        roles=frozenset({UserRole.ADMIN}),
    )
    denied = client.get("/test/role", headers={"X-Request-ID": "role-denied"})

    assert allowed.status_code == 200
    assert allowed.json() == {"username": "role-test"}
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"
    assert denied.json()["error"]["request_id"] == denied.headers["x-request-id"]
