"""Demo 主数据 Seed 的安全、幂等和不重置行为测试。"""

import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.config import Settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.models.master_data import MasterDataEntity
from scripts import seed_demo_master_data as seed_module


def test_demo_master_data_seed_refuses_production_before_database_access() -> None:
    """验证 production 环境在调用 Session Factory 前拒绝 Demo seed。"""
    session_factory = Mock()
    with pytest.raises(RuntimeError, match="disabled in production"):
        asyncio.run(
            seed_module.seed_demo_master_data(
                Settings(_env_file=None, app_env="production"), session_factory
            )
        )
    session_factory.assert_not_called()


def test_postgres_demo_seed_is_create_only_and_preserves_existing_data(monkeypatch) -> None:
    """验证 PostgreSQL Seed 幂等创建实体且绝不重置已有快照或版本。"""

    async def run() -> None:
        """使用随机实体身份执行两次 seed 和已修改实体保护验证。"""
        engine = create_db_engine(Settings())
        factory = create_session_factory(engine)
        entity_key = f"seed-{uuid4().hex}"
        monkeypatch.setattr(seed_module, "_DEMO_ENTITY_KEY", entity_key)
        try:
            assert await seed_module.seed_demo_master_data(Settings(_env_file=None), factory)
            assert not await seed_module.seed_demo_master_data(Settings(_env_file=None), factory)
            async with factory() as session:
                entity = await session.scalar(
                    select(MasterDataEntity).where(MasterDataEntity.entity_key == entity_key)
                )
                assert entity is not None
                entity.data_json = {"name": "changed after seed"}
                entity.version = 7
                await session.commit()

            assert not await seed_module.seed_demo_master_data(Settings(_env_file=None), factory)
            async with factory() as session:
                entity = await session.scalar(
                    select(MasterDataEntity).where(MasterDataEntity.entity_key == entity_key)
                )
                assert entity is not None
                assert entity.data_json == {"name": "changed after seed"}
                assert entity.version == 7
        finally:
            async with factory() as session:
                await session.execute(
                    delete(MasterDataEntity).where(MasterDataEntity.entity_key == entity_key)
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
