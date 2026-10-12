import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.infrastructure.database import database_url
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migration 與 runtime 共用同一套 Base metadata 和 database URL builder，避免定义分叉。
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """使用统一 URL 与 Base metadata 配置离线迁移，不建立数据库连接。"""
    context.configure(
        url=database_url(get_settings()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """在同步迁移上下文中绑定异步引擎提供的连接并运行迁移。"""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """按 Settings 创建临时异步引擎运行在线迁移，并在结束后释放引擎。"""
    engine = create_async_engine(database_url(get_settings()), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
