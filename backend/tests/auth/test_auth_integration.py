"""认证服务与 PostgreSQL、HTTP API 的集成测试。"""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import get_auth_service, get_db_session, require_any_role
from app.auth.schemas import LoginRequest
from app.auth.security import _DUMMY_PASSWORD_HASH, PasswordHasher, hash_bearer_token
from app.auth.service import AuthService
from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.domain.auth.enums import UserRole
from app.infrastructure.database import create_db_engine, create_session_factory
from app.main import create_app
from app.models.auth import AuthSession, User, UserRoleAssignment
from scripts.seed_demo_users import seed_demo_users


def test_auth_persistence_and_http_lifecycle() -> None:
    """验证用户/角色/会话持久化、登录、可信上下文、权限、登出及错误 envelope。"""

    async def run() -> None:
        """在隔离用户名下执行完整认证集成路径并清理数据库记录。"""
        settings = Settings(auth_session_ttl_minutes=3)
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        username = f"review-{secrets.token_hex(6)}"
        password = "integration-demo-password"
        hasher = PasswordHasher()
        admin_id = None

        class RecordingHasher(PasswordHasher):
            """记录验证摘要以确认未知和禁用账号遵守密码校验顺序。"""

            def __init__(self) -> None:
                """初始化记录容器和 Argon2id 验证器。"""
                super().__init__()
                self.verified_hashes: list[str] = []

            def verify(self, password: str, password_hash: str) -> bool:
                """记录被验证的摘要并执行实际密码验证。"""
                self.verified_hashes.append(password_hash)
                return super().verify(password, password_hash)

        recording_hasher = RecordingHasher()
        app = create_app(settings)
        app.state.db_session_factory = factory

        async def auth_service_override(
            session=Depends(get_db_session),
        ) -> AuthService:
            """为集成请求注入可观测的 Argon2id 验证器。"""
            return AuthService(session, settings, recording_hasher)

        app.dependency_overrides[get_auth_service] = auth_service_override
        app_router = RequestIdMiddleware(app, settings)
        transport = httpx.ASGITransport(app=app_router)
        try:
            async with factory() as session:
                user = User(
                    username=username,
                    display_name="Integration User",
                    password_hash=await asyncio.to_thread(hasher.hash, password),
                )
                user.role_assignments.append(UserRoleAssignment(role=UserRole.APPROVER))
                session.add(user)
                await session.commit()
                user_id = user.id

            async with factory() as session:
                session.add(
                    User(
                        username=username,
                        display_name="Duplicate Username",
                        password_hash="not-a-real-password-hash",
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                unknown = await client.post(
                    "/api/v1/auth/login",
                    json={"username": f"absent-{username}", "password": password},
                    headers={"X-Request-ID": "auth-unknown-user"},
                )
                wrong_password = await client.post(
                    "/api/v1/auth/login",
                    json={"username": username, "password": "wrong-password"},
                    headers={"X-Request-ID": "auth-wrong-password"},
                )
                assert unknown.status_code == wrong_password.status_code == 401
                assert unknown.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
                assert wrong_password.json()["error"]["code"] == unknown.json()["error"]["code"]
                assert unknown.headers["www-authenticate"] == "Bearer"
                assert unknown.json()["error"]["request_id"] == "auth-unknown-user"
                assert unknown.headers["x-request-id"] == "auth-unknown-user"
                assert recording_hasher.verified_hashes[0] == _DUMMY_PASSWORD_HASH
                assert recording_hasher.verified_hashes.count(_DUMMY_PASSWORD_HASH) == 1

                disabled_id = user_id
                async with factory() as session:
                    disabled_user = await session.get(User, disabled_id)
                    assert disabled_user is not None
                    disabled_user.enabled = False
                    await session.commit()
                disabled = await client.post(
                    "/api/v1/auth/login",
                    json={"username": username, "password": password},
                )
                assert disabled.status_code == 401
                assert disabled.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
                assert recording_hasher.verified_hashes[-1] == user.password_hash
                async with factory() as session:
                    disabled_user = await session.get(User, disabled_id)
                    assert disabled_user is not None
                    disabled_user.enabled = True
                    await session.commit()

                login = await client.post(
                    "/api/v1/auth/login",
                    json={"username": f"  {username.upper()}  ", "password": password},
                    headers={"X-Request-ID": "auth-login"},
                )
                assert login.status_code == 200
                assert login.headers["cache-control"] == "no-store"
                assert login.json()["expires_in"] == 180
                assert isinstance(login.json()["expires_in"], int)
                assert login.json()["expires_in"] >= 0
                assert login.json()["user"]["roles"] == ["APPROVER"]
                assert not {"password", "password_hash", "token_hash"} & set(login.json())
                raw_token = login.json()["access_token"]
                async with factory() as session:
                    stored_session = await session.scalar(
                        select(AuthSession).where(AuthSession.user_id == user_id)
                    )
                    assert stored_session is not None
                    assert stored_session.token_hash == hash_bearer_token(raw_token)
                    assert stored_session.token_hash != raw_token
                    assert stored_session.created_at.tzinfo is not None
                    assert stored_session.expires_at.tzinfo is not None

                me = await client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {raw_token}"},
                )
                assert me.status_code == 200
                assert me.json()["id"] == str(user_id)
                assert me.json()["roles"] == ["APPROVER"]
                assert not {"password", "password_hash", "token_hash", "access_token"} & set(
                    me.json()
                )

                async with factory() as session:
                    disabled_user = await session.get(User, user_id)
                    assert disabled_user is not None
                    disabled_user.enabled = False
                    await session.commit()
                disabled_session = await client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {raw_token}"},
                )
                assert disabled_session.status_code == 403
                assert disabled_session.json()["error"]["code"] == "AUTH_USER_DISABLED"
                async with factory() as session:
                    disabled_user = await session.get(User, user_id)
                    assert disabled_user is not None
                    disabled_user.enabled = True
                    await session.commit()

                app.add_api_route(
                    "/test/approver",
                    approver_probe,
                    dependencies=[Depends(require_any_role(UserRole.APPROVER))],
                )
                allowed = await client.get(
                    "/test/approver", headers={"Authorization": f"Bearer {raw_token}"}
                )
                assert allowed.status_code == 200

                admin_username = f"admin-{username}"
                async with factory() as session:
                    admin_user = User(
                        username=admin_username,
                        display_name="Demo Admin",
                        password_hash=await asyncio.to_thread(hasher.hash, password),
                    )
                    admin_user.role_assignments.append(UserRoleAssignment(role=UserRole.ADMIN))
                    session.add(admin_user)
                    await session.commit()
                    admin_id = admin_user.id
                admin_login = await client.post(
                    "/api/v1/auth/login",
                    json={"username": admin_username, "password": password},
                )
                admin_token = admin_login.json()["access_token"]
                admin_denied = await client.get(
                    "/test/approver",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                assert admin_denied.status_code == 403
                assert admin_denied.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"

                logout = await client.post(
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {raw_token}"},
                )
                assert logout.status_code == 204
                after_logout = await client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {raw_token}"},
                )
                assert after_logout.status_code == 401
                assert after_logout.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"

                missing = await client.get("/api/v1/auth/me")
                assert missing.status_code == 401
                assert missing.headers["www-authenticate"] == "Bearer"

            async with factory() as session:
                expired = AuthSession(
                    user_id=user_id,
                    token_hash=hash_bearer_token("expired-token"),
                    created_at=datetime.now(UTC) - timedelta(hours=2),
                    expires_at=datetime.now(UTC) - timedelta(hours=1),
                )
                revoked = AuthSession(
                    user_id=user_id,
                    token_hash=hash_bearer_token("revoked-token"),
                    created_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    revoked_at=datetime.now(UTC),
                )
                session.add_all([expired, revoked])
                await session.commit()
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                expired_response = await client.get(
                    "/api/v1/auth/me", headers={"Authorization": "Bearer expired-token"}
                )
                revoked_response = await client.get(
                    "/api/v1/auth/me", headers={"Authorization": "Bearer revoked-token"}
                )
                assert expired_response.json()["error"]["code"] == "AUTH_TOKEN_EXPIRED"
                assert revoked_response.json()["error"]["code"] == "AUTH_UNAUTHENTICATED"
                assert expired_response.headers["www-authenticate"] == "Bearer"
        finally:
            async with factory() as session:
                if admin_id is not None:
                    await session.execute(
                        delete(AuthSession).where(AuthSession.user_id == admin_id)
                    )
                    await session.execute(
                        delete(UserRoleAssignment).where(UserRoleAssignment.user_id == admin_id)
                    )
                    await session.execute(delete(User).where(User.id == admin_id))
                await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
                await session.execute(
                    delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id)
                )
                await session.execute(delete(User).where(User.id == user_id))
                await session.commit()
            await engine.dispose()

    async def approver_probe() -> dict[str, str]:
        """作为测试专用 endpoint 返回已通过角色依赖的状态。"""
        return {"status": "ok"}

    asyncio.run(run())


def test_auth_unique_role_check_and_relationship_constraints() -> None:
    """验证角色 CHECK/组合主键及 Token 摘要唯一约束由 PostgreSQL 强制。"""

    async def run() -> None:
        """插入认证行并对非法角色、重复分配与重复摘要执行回滚断言。"""
        engine = create_db_engine(Settings())
        factory = create_session_factory(engine)
        user = User(
            username=f"constraints-{secrets.token_hex(6)}",
            display_name="Constraint User",
            password_hash="not-a-real-password-hash",
        )
        try:
            async with factory() as session:
                session.add(user)
                await session.flush()
                user_id = user.id
                session.add(UserRoleAssignment(user_id=user_id, role=UserRole.APPLICANT))
                await session.commit()

            async with factory() as session:
                session.add(UserRoleAssignment(user_id=user_id, role=UserRole.APPLICANT))
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                session.add(
                    UserRoleAssignment(user_id=user_id, role="INVALID_ROLE")  # type: ignore[arg-type]
                )
                with pytest.raises(IntegrityError) as error_info:
                    await session.flush()
                assert 'check constraint "ck_user_roles_role"' in str(error_info.value.orig)
                await session.rollback()

            async with factory() as session:
                session.add_all(
                    [
                        AuthSession(
                            user_id=user_id,
                            token_hash="a" * 64,
                            created_at=datetime.now(UTC),
                            expires_at=datetime.now(UTC) + timedelta(hours=1),
                        ),
                        AuthSession(
                            user_id=user_id,
                            token_hash="a" * 64,
                            created_at=datetime.now(UTC),
                            expires_at=datetime.now(UTC) + timedelta(hours=1),
                        ),
                    ]
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()
        finally:
            async with factory() as session:
                await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
                await session.execute(
                    delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
                )
                await session.execute(delete(User).where(User.id == user.id))
                await session.commit()
            await engine.dispose()

    asyncio.run(run())


def test_login_failure_rolls_back_session_creation() -> None:
    """验证登录 Session 写入阶段失败时事务回滚，不留下半成品记录。"""

    async def run() -> None:
        """注入 session flush 后异常，确认 AuthService 事务撤销写入。"""
        engine = create_db_engine(Settings())
        factory = create_session_factory(engine)
        username = f"rollback-{secrets.token_hex(6)}"
        hasher = PasswordHasher()
        async with factory() as session:
            user = User(
                username=username,
                display_name="Rollback User",
                password_hash=await asyncio.to_thread(hasher.hash, "rollback-password"),
            )
            session.add(user)
            await session.commit()
            user_id = user.id
        service = None
        try:
            async with factory() as session:
                service = AuthService(session, Settings(), hasher)
                original = service._repository.create_session

                async def fail_after_insert(auth_session: AuthSession) -> AuthSession:
                    """模拟 flush 成功后应用层异常以验证外层事务回滚。"""
                    await original(auth_session)
                    raise RuntimeError("injected failure")

                service._repository.create_session = fail_after_insert  # type: ignore[method-assign]
                with pytest.raises(RuntimeError, match="injected failure"):
                    await service.login(
                        LoginRequest(username=username, password="rollback-password")
                    )
            async with factory() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(AuthSession)
                    .where(AuthSession.user_id == user_id)
                )
                assert count == 0

            async with factory() as session:
                service = AuthService(session, Settings(), hasher)
                login = await service.login(
                    LoginRequest(username=username, password="rollback-password")
                )
                auth_context = await service.authenticate(login.access_token)
                original_revoke = service._repository.revoke_session

                async def fail_after_revoke(
                    auth_session: AuthSession, revoked_at: datetime
                ) -> None:
                    """模拟撤销 flush 后故障，要求服务事务恢复未撤销状态。"""
                    await original_revoke(auth_session, revoked_at)
                    raise RuntimeError("injected revoke failure")

                service._repository.revoke_session = fail_after_revoke  # type: ignore[method-assign]
                with pytest.raises(RuntimeError, match="injected revoke failure"):
                    await service.logout(auth_context)
            async with factory() as session:
                persisted = await session.scalar(
                    select(AuthSession).where(AuthSession.user_id == user_id)
                )
                assert persisted is not None
                assert persisted.revoked_at is None
        finally:
            async with factory() as session:
                await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
                await session.execute(delete(User).where(User.id == user_id))
                await session.commit()
            await engine.dispose()

    asyncio.run(run())


def test_demo_seed_is_idempotent_and_reconciles_exact_roles() -> None:
    """验证 Demo seed 重复运行不复制用户/角色且纠正不匹配角色。"""

    async def run() -> None:
        """运行两次 seed，检查哈希保留和角色集合收敛后恢复原数据。"""
        settings = Settings(
            _env_file=None,
            app_env="test",
            demo_user_password="seed-integration-password",
        )
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        expected = {
            "applicant": UserRole.APPLICANT,
            "manager": UserRole.APPROVER,
            "data_admin": UserRole.DATA_ADMIN,
            "admin": UserRole.ADMIN,
        }
        snapshots: dict[str, tuple[UUID, str, str, bool, datetime, datetime, list[UserRole]]] = {}
        demo_ids: dict[str, UUID] = {}
        try:
            async with factory() as session:
                existing_users = (
                    (
                        await session.execute(
                            select(User)
                            .options(selectinload(User.role_assignments))
                            .where(User.username.in_(expected))
                        )
                    )
                    .scalars()
                    .all()
                )
                snapshots = {
                    user.username: (
                        user.id,
                        user.display_name,
                        user.password_hash,
                        user.enabled,
                        user.created_at,
                        user.updated_at,
                        [assignment.role for assignment in user.role_assignments],
                    )
                    for user in existing_users
                }

            assert await seed_demo_users(settings, factory) == 4
            async with factory() as session:
                first_run = (
                    (
                        await session.execute(
                            select(User)
                            .options(selectinload(User.role_assignments))
                            .where(User.username.in_(expected))
                        )
                    )
                    .scalars()
                    .all()
                )
                first_hashes = {user.username: user.password_hash for user in first_run}
                demo_ids = {user.username: user.id for user in first_run}
                for user in first_run:
                    assert {assignment.role for assignment in user.role_assignments} == {
                        expected[user.username]
                    }

            async with factory() as session:
                manager = await session.scalar(
                    select(User)
                    .options(selectinload(User.role_assignments))
                    .where(User.username == "manager")
                )
                assert manager is not None
                manager.role_assignments.append(UserRoleAssignment(role=UserRole.ADMIN))
                await session.commit()

            assert await seed_demo_users(settings, factory) == 4
            async with factory() as session:
                second_run = (
                    (
                        await session.execute(
                            select(User)
                            .options(selectinload(User.role_assignments))
                            .where(User.username.in_(expected))
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(second_run) == 4
                for user in second_run:
                    assert {assignment.role for assignment in user.role_assignments} == {
                        expected[user.username]
                    }
                    assert user.password_hash == first_hashes[user.username]
                assignment_count = await session.scalar(
                    select(func.count())
                    .select_from(UserRoleAssignment)
                    .where(UserRoleAssignment.user_id.in_(demo_ids.values()))
                )
                assert assignment_count == 4
        finally:
            async with factory() as session:
                for username, user_id in demo_ids.items():
                    snapshot = snapshots.get(username)
                    if snapshot is None:
                        await session.execute(
                            delete(AuthSession).where(AuthSession.user_id == user_id)
                        )
                        await session.execute(
                            delete(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id)
                        )
                        await session.execute(delete(User).where(User.id == user_id))
                    else:
                        user = await session.scalar(
                            select(User)
                            .options(selectinload(User.role_assignments))
                            .where(User.id == user_id)
                        )
                        assert user is not None
                        _, display_name, password_hash, enabled, created_at, updated_at, roles = (
                            snapshot
                        )
                        user.display_name = display_name
                        user.password_hash = password_hash
                        user.enabled = enabled
                        user.created_at = created_at
                        user.updated_at = updated_at
                        for assignment in list(user.role_assignments):
                            await session.delete(assignment)
                        for role in roles:
                            session.add(UserRoleAssignment(user=user, role=role))
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
