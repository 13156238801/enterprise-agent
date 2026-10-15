"""创建首次启动所需的虚构供应商主数据实体。"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings, get_settings
from app.domain.master_data.diff import validate_json_object
from app.infrastructure.database import create_db_engine, create_session_factory
from app.master_data.repository import MasterDataRepository
from app.models.master_data import MasterDataEntity

_DEMO_ENTITY_TYPE = "supplier"
_DEMO_ENTITY_KEY = "supplier_a"
_DEMO_ENTITY_DATA: dict[str, object] = {
    "name": "Demo Supplier A",
    "payment_terms": "PREPAID",
    "currency": "CNY",
    "enabled": True,
}


async def seed_demo_master_data(
    settings: Settings,
    session_factory: async_sessionmaker,
) -> bool:
    """仅在实体不存在时创建 Demo supplier，返回本次是否新增。"""
    if settings.app_env == "production":
        raise RuntimeError("Demo master data seed is disabled in production")
    data = validate_json_object(_DEMO_ENTITY_DATA, "demo_data")
    async with session_factory() as session:
        async with session.begin():
            repository = MasterDataRepository(session)
            existing = await repository.get_entity(_DEMO_ENTITY_TYPE, _DEMO_ENTITY_KEY)
            if existing is not None:
                # 后续审批可能已合法改变 Demo 实体，Seed 仅确保存在而不重置。
                return False
            await repository.add_entity(
                MasterDataEntity(
                    entity_type=_DEMO_ENTITY_TYPE,
                    entity_key=_DEMO_ENTITY_KEY,
                    data_json=data,
                    version=1,
                )
            )
    return True


async def main() -> None:
    """读取配置执行独立 Demo Seed，并在 finally 中释放 Engine。"""
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("Demo master data seed is disabled in production")
    engine = create_db_engine(settings)
    try:
        created = await seed_demo_master_data(settings, create_session_factory(engine))
        print("Created supplier/supplier_a." if created else "Verified supplier/supplier_a.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
