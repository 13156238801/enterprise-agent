"""创建或验证本地 Demo 工作流定义。"""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings, get_settings
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.infrastructure.database import create_db_engine, create_session_factory
from app.workflow.repository import WorkflowDefinitionRepository

_DEMO_WORKFLOW = WorkflowDefinitionSpec(
    code="supplier_change",
    version=1,
    nodes=(
        WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
        WorkflowNodeDefinition("data_admin_review", UserRole.DATA_ADMIN),
    ),
)


async def seed_demo_workflows(settings: Settings, session_factory: async_sessionmaker) -> bool:
    """幂等创建或验证 Demo v1，返回本次是否新增了版本。"""
    if settings.app_env == "production":
        raise RuntimeError("Demo workflow seed is disabled in production")

    async with session_factory() as session:
        async with session.begin():
            repository = WorkflowDefinitionRepository(session)
            versions = await repository.list_versions(_DEMO_WORKFLOW.code)
            existing = await repository.get_by_code_version(
                _DEMO_WORKFLOW.code, _DEMO_WORKFLOW.version
            )
            if existing is None:
                if versions:
                    raise RuntimeError(
                        "workflow history already exists; refusing retroactive v1 insertion"
                    )
                await repository.add(_DEMO_WORKFLOW, active=True)
                return True

            if existing != _DEMO_WORKFLOW:
                raise RuntimeError(
                    "existing workflow version differs; create a new version instead"
                )

            active = await repository.get_active(_DEMO_WORKFLOW.code)
            if active is None:
                await repository.activate_if_none(_DEMO_WORKFLOW.code, _DEMO_WORKFLOW.version)
    return False


async def main() -> None:
    """读取环境配置运行 Demo workflow seed 并释放临时 Engine。"""
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("Demo workflow seed is disabled in production")
    engine = create_db_engine(settings)
    try:
        created = await seed_demo_workflows(settings, create_session_factory(engine))
        print("Created supplier_change v1." if created else "Verified supplier_change v1.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
