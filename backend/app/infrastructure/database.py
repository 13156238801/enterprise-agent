from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings


class Base(DeclarativeBase):
    """所有 ORM metadata 的唯一基类；本任务不定义业务表。"""


def database_url(settings: Settings) -> URL:
    """安全构造运行时和 Alembic 共用的 PostgreSQL URL，不拼接或记录明文 DSN。"""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_db_engine(settings: Settings) -> AsyncEngine:
    """创建应用 worker 复用的异步 Engine；连接池由调用方在 lifespan 中释放。"""
    # Engine 持有连接池，作为每个 worker 的进程级资源复用，避免逐请求创建连接池。
    return create_async_engine(database_url(settings), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """为共享 Engine 创建 Session 工厂，每次调用工厂会得到独立 AsyncSession。"""
    # 工厂可复用，但每次调用都创建独立 Session；Session 自身不可跨请求并发共享。
    return async_sessionmaker(engine, expire_on_commit=False)
