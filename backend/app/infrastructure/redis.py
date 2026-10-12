from redis.asyncio import Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    """按 Settings 创建 worker 级异步 Redis client；网络连接按需建立，由 lifespan 关闭。"""
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )
