"""MasterDataEntity 的 PostgreSQL 唯一性、CHECK 和 JSONB 持久化测试。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.models.master_data import MasterDataEntity


def test_postgres_master_data_entity_constraints_and_json_roundtrip() -> None:
    """验证 PostgreSQL 实际约束、JSONB roundtrip、版本默认值和时区字段。"""

    async def run() -> None:
        """创建随机实体，尝试非法写入并清理测试记录。"""
        engine = create_db_engine(get_settings())
        factory = create_session_factory(engine)
        entity_type = "test_entity"
        entity_key = f"key-{uuid4().hex}"
        entity = MasterDataEntity(
            entity_type=entity_type,
            entity_key=entity_key,
            data_json={"nested": {"values": [1, None, "x"]}},
        )
        try:
            async with factory() as session:
                session.add(entity)
                await session.commit()
                entity_id = entity.id

            async with factory() as session:
                persisted = await session.get(MasterDataEntity, entity_id)
                assert persisted is not None
                assert persisted.version == 1
                assert persisted.data_json == {"nested": {"values": [1, None, "x"]}}
                assert persisted.created_at.tzinfo is not None
                assert persisted.updated_at.tzinfo is not None

            async with factory() as session:
                session.add(
                    MasterDataEntity(
                        entity_type=entity_type,
                        entity_key=entity_key,
                        data_json={},
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                with pytest.raises(IntegrityError):
                    await session.execute(
                        insert(MasterDataEntity).values(
                            id=uuid4(),
                            entity_type=entity_type,
                            entity_key=f"invalid-{uuid4().hex}",
                            data_json={},
                            version=0,
                        )
                    )
                    await session.flush()
                await session.rollback()
        finally:
            async with factory() as session:
                await session.execute(
                    delete(MasterDataEntity).where(
                        MasterDataEntity.entity_type == entity_type,
                        MasterDataEntity.entity_key == entity_key,
                    )
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
