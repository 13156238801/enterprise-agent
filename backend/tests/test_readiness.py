import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.request_context import RequestIdMiddleware
from app.main import create_app


class HealthyConnection:
    async def execute(self, statement) -> None:
        """验证 readiness 对数据库连接执行 SELECT 1。"""
        assert str(statement) == "SELECT 1"


class HealthyEngine:
    def connect(self):
        """提供成功的异步连接上下文供 readiness 检查使用。"""

        class ConnectionContext:
            async def __aenter__(self):
                """模拟获取数据库连接。"""
                return HealthyConnection()

            async def __aexit__(self, *_args):
                """模拟释放 readiness 使用的数据库连接。"""
                return None

        return ConnectionContext()

    async def dispose(self) -> None:
        """模拟释放数据库 Engine。"""
        return None


class HealthyRedis:
    async def ping(self) -> bool:
        """模拟 Redis PING 成功。"""
        return True

    async def aclose(self) -> None:
        """模拟关闭 Redis client。"""
        return None


def app_with_resources(engine, redis, monkeypatch):
    """构建使用替身资源并保留生产 Request ID middleware 的测试应用。"""
    monkeypatch.setattr("app.main.create_db_engine", lambda settings: engine)
    monkeypatch.setattr("app.main.create_session_factory", lambda db_engine: object())
    monkeypatch.setattr("app.main.create_redis_client", lambda settings: redis)
    app = create_app(Settings(_env_file=None))
    return RequestIdMiddleware(app, Settings(_env_file=None))


def test_health_stays_healthy_when_dependencies_fail(monkeypatch) -> None:
    """验证依赖不可用时 liveness 仍返回原有成功响应。"""

    class Unavailable:
        async def ping(self):
            """模拟包含敏感信息的 Redis 探测异常。"""
            raise RuntimeError("password=sensitive")

        async def aclose(self):
            """模拟关闭 Redis client。"""
            return None

    class UnavailableEngine:
        def connect(self):
            """模拟包含 DSN 的数据库连接异常。"""
            raise RuntimeError("postgresql://user:password@host/db")

        async def dispose(self):
            """模拟释放数据库 Engine。"""
            return None

    with TestClient(app_with_resources(UnavailableEngine(), Unavailable(), monkeypatch)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_success_uses_existing_resources_and_returns_request_id(monkeypatch) -> None:
    """验证 readiness 复用 lifespan 资源并返回状态和 Request ID。"""
    engine = HealthyEngine()
    redis = HealthyRedis()

    with TestClient(app_with_resources(engine, redis, monkeypatch)) as client:
        response = client.get("/ready", headers={"X-Request-ID": "ready-check"})

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "postgres": "ok", "redis": "ok"}
    assert response.headers["X-Request-ID"] == "ready-check"


@pytest.mark.parametrize("failed_dependency", ["postgres", "redis"])
def test_readiness_dependency_failure_is_sanitized_and_returns_request_id(
    failed_dependency: str, monkeypatch
) -> None:
    """验证任一依赖失败时返回 503、稳定字段且不泄露错误详情。"""

    class FailingRedis:
        async def ping(self):
            """模拟带敏感信息的 Redis 探测错误。"""
            raise RuntimeError("redis password=do-not-leak")

        async def aclose(self):
            """模拟关闭 Redis client。"""
            return None

    class FailingEngine:
        def connect(self):
            """模拟包含私有主机和凭据的数据库异常。"""
            raise RuntimeError("postgresql://user:secret@private-host/db")

        async def dispose(self):
            """模拟释放数据库 Engine。"""
            return None

    engine = FailingEngine() if failed_dependency == "postgres" else HealthyEngine()
    redis = FailingRedis() if failed_dependency == "redis" else HealthyRedis()

    with TestClient(app_with_resources(engine, redis, monkeypatch)) as client:
        response = client.get("/ready", headers={"X-Request-ID": f"failed-{failed_dependency}"})

    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == f"failed-{failed_dependency}"
    assert response.json() == {
        "status": "not_ready",
        "postgres": "error" if failed_dependency == "postgres" else "ok",
        "redis": "error" if failed_dependency == "redis" else "ok",
    }
    assert all(
        secret not in response.text
        for secret in ("do-not-leak", "secret", "private-host", "postgresql://", "Traceback")
    )


def test_readiness_checks_dependencies_concurrently(monkeypatch) -> None:
    """验证 PostgreSQL 与 Redis 探测并发执行而不累加等待时间。"""

    class SlowEngine:
        def connect(self):
            """返回延迟的数据库连接上下文。"""

            class ConnectionContext:
                async def __aenter__(self):
                    """模拟有延迟的数据库连接建立。"""
                    await asyncio.sleep(0.2)
                    return HealthyConnection()

                async def __aexit__(self, *_args):
                    """模拟关闭数据库连接。"""
                    return None

            return ConnectionContext()

        async def dispose(self):
            """模拟释放数据库 Engine。"""
            return None

    class SlowRedis:
        async def ping(self):
            """模拟有延迟的 Redis PING 成功。"""
            await asyncio.sleep(0.2)
            return True

        async def aclose(self):
            """模拟关闭 Redis client。"""
            return None

    with TestClient(app_with_resources(SlowEngine(), SlowRedis(), monkeypatch)) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.elapsed.total_seconds() < 0.35


def test_readiness_checks_have_explicit_timeout(monkeypatch) -> None:
    """验证 Redis 探测超时会转换为安全的 not-ready 响应。"""

    class HangingRedis:
        async def ping(self):
            """模拟超过 readiness timeout 的 Redis 请求。"""
            await asyncio.sleep(5)

        async def aclose(self):
            """模拟关闭 Redis client。"""
            return None

    with TestClient(app_with_resources(HealthyEngine(), HangingRedis(), monkeypatch)) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["redis"] == "error"
    assert response.elapsed.total_seconds() < 2.5


def test_readiness_access_log_is_generated(caplog, monkeypatch) -> None:
    """验证 readiness 请求正常产生结构化 access log。"""
    caplog.set_level(logging.INFO, logger="enterprise_agent.access")
    with TestClient(app_with_resources(HealthyEngine(), HealthyRedis(), monkeypatch)) as client:
        response = client.get("/ready")
    logs = [record for record in caplog.records if record.name == "enterprise_agent.access"]
    assert response.status_code == 200
    assert len(logs) == 1
    assert logs[0].path == "/ready"
    assert logs[0].status_code == 200
