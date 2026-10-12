from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_lifespan_creates_and_releases_application_resources(monkeypatch) -> None:
    """通过 TestClient 生命周期验证资源创建、state 持有及正常释放顺序。"""
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            """记录数据库 Engine 释放事件。"""
            events.append("dispose_db")

    class RedisClient:
        async def aclose(self) -> None:
            """记录 Redis client 关闭事件。"""
            events.append("close_redis")

    engine = Engine()
    redis = RedisClient()
    monkeypatch.setattr(
        "app.main.create_db_engine", lambda settings: (events.append("create_db") or engine)
    )
    monkeypatch.setattr("app.main.create_session_factory", lambda db_engine: "session_factory")
    monkeypatch.setattr(
        "app.main.create_redis_client", lambda settings: (events.append("create_redis") or redis)
    )

    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        assert client.app.state.db_engine is engine
        assert client.app.state.db_session_factory == "session_factory"
        assert client.app.state.redis is redis
        assert events == ["create_db", "create_redis"]

    assert events == ["create_db", "create_redis", "close_redis", "dispose_db"]


def test_lifespan_cleans_up_resources_when_one_cleanup_fails(monkeypatch) -> None:
    """验证 Redis 释放失败时 lifespan 仍会继续释放数据库 Engine。"""
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            """记录数据库 Engine 释放事件。"""
            events.append("dispose_db")

    class RedisClient:
        async def aclose(self) -> None:
            """记录关闭尝试后模拟 Redis 释放失败。"""
            events.append("close_redis")
            raise RuntimeError("redis cleanup failed")

    monkeypatch.setattr("app.main.create_db_engine", lambda settings: Engine())
    monkeypatch.setattr("app.main.create_session_factory", lambda db_engine: object())
    monkeypatch.setattr("app.main.create_redis_client", lambda settings: RedisClient())

    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert events == ["close_redis", "dispose_db"]


def test_lifespan_partial_startup_cleans_up_previously_created_resource(monkeypatch) -> None:
    """验证 Redis 初始化失败时，已创建的数据库 Engine 仍会被清理。"""
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            """记录部分启动失败后的数据库 Engine 清理。"""
            events.append("dispose_db")

    monkeypatch.setattr("app.main.create_db_engine", lambda settings: Engine())
    monkeypatch.setattr("app.main.create_session_factory", lambda db_engine: object())

    def fail_redis(settings):
        """模拟 Redis 初始化失败以触发部分启动清理路径。"""
        raise RuntimeError("controlled startup failure")

    monkeypatch.setattr("app.main.create_redis_client", fail_redis)
    app = create_app(Settings(_env_file=None))

    try:
        with TestClient(app):
            raise AssertionError("startup should fail")
    except RuntimeError as error:
        assert str(error) == "controlled startup failure"

    assert events == ["dispose_db"]
