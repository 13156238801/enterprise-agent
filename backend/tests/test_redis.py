from redis.asyncio import Redis

from app.core.config import Settings
from app.infrastructure.redis import create_redis_client


def test_redis_settings_and_client_parameters(monkeypatch) -> None:
    """验证 Redis 配置默认值、环境覆盖及 client 连接参数。"""
    defaults = Settings(_env_file=None)
    assert defaults.redis_host == "localhost"
    assert defaults.redis_port == 6379
    assert defaults.redis_db == 0

    monkeypatch.setenv("REDIS_HOST", "cache.example")
    monkeypatch.setenv("REDIS_PORT", "6381")
    monkeypatch.setenv("REDIS_DB", "4")
    settings = Settings(_env_file=None)
    assert settings.redis_host == "cache.example"
    assert settings.redis_port == 6381
    assert settings.redis_db == 4

    client = create_redis_client(settings)
    assert isinstance(client, Redis)
    assert client.connection_pool.connection_kwargs["host"] == "cache.example"
    assert client.connection_pool.connection_kwargs["port"] == 6381
    assert client.connection_pool.connection_kwargs["db"] == 4
