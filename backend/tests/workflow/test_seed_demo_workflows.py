"""Demo 工作流 Seed 的安全边界测试。"""

import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.config import Settings
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.infrastructure.database import create_db_engine, create_session_factory
from app.models.workflow import WorkflowDefinition
from scripts import seed_demo_workflows as seed_module


def test_workflow_seed_refuses_production_before_database_access() -> None:
    """验证 production 环境在调用 Session Factory 前拒绝 Seed。"""
    session_factory = Mock()

    with pytest.raises(RuntimeError, match="disabled in production"):
        asyncio.run(
            seed_module.seed_demo_workflows(
                Settings(_env_file=None, app_env="production"), session_factory
            )
        )

    session_factory.assert_not_called()


def test_postgres_workflow_seed_is_idempotent_and_preserves_version_history(monkeypatch) -> None:
    """验证 PostgreSQL Seed 幂等、不可变冲突及 active 历史版本保护。"""
    suffix = uuid4().hex[:16]
    code = f"seed_workflow_{suffix}"
    other_code = f"seed_history_{suffix}"
    expected = WorkflowDefinitionSpec(
        code=code,
        version=1,
        nodes=(WorkflowNodeDefinition("manager_review", UserRole.APPROVER),),
    )
    settings = Settings(_env_file=None, app_env="test")
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    monkeypatch.setattr(seed_module, "_DEMO_WORKFLOW", expected)

    async def run() -> None:
        """执行 Seed 各分支并在 finally 中删除测试专属 code。"""
        try:
            assert await seed_module.seed_demo_workflows(settings, factory)
            assert not await seed_module.seed_demo_workflows(settings, factory)
            async with factory() as session:
                versions = list(
                    (
                        await session.scalars(
                            select(WorkflowDefinition).where(WorkflowDefinition.code == code)
                        )
                    ).all()
                )
                assert len(versions) == 1
                assert versions[0].active
                versions[0].active = False
                await session.flush()
                session.add(
                    WorkflowDefinition(
                        code=code,
                        version=2,
                        definition_json={
                            "code": code,
                            "version": 2,
                            "nodes": [{"key": "new_review", "role": "APPROVER"}],
                        },
                        active=True,
                    )
                )
                await session.commit()

            assert not await seed_module.seed_demo_workflows(settings, factory)
            async with factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(WorkflowDefinition)
                            .where(WorkflowDefinition.code == code)
                            .order_by(WorkflowDefinition.version)
                        )
                    ).all()
                )
                assert [(row.version, row.active) for row in rows] == [(1, False), (2, True)]
                rows[1].active = False
                await session.commit()

            assert not await seed_module.seed_demo_workflows(settings, factory)
            async with factory() as session:
                active = await session.scalar(
                    select(WorkflowDefinition).where(
                        WorkflowDefinition.code == code,
                        WorkflowDefinition.active.is_(True),
                    )
                )
                assert active is not None and active.version == 1

            monkeypatch.setattr(
                seed_module,
                "_DEMO_WORKFLOW",
                WorkflowDefinitionSpec(
                    code=code,
                    version=1,
                    nodes=(WorkflowNodeDefinition("different_review", UserRole.APPROVER),),
                ),
            )
            with pytest.raises(RuntimeError, match="create a new version"):
                await seed_module.seed_demo_workflows(settings, factory)

            later_v2 = WorkflowDefinitionSpec(
                code=other_code,
                version=2,
                nodes=(WorkflowNodeDefinition("later_review", UserRole.DATA_ADMIN),),
            )
            monkeypatch.setattr(seed_module, "_DEMO_WORKFLOW", later_v2)
            async with factory() as session:
                session.add(
                    WorkflowDefinition(
                        code=other_code,
                        version=2,
                        definition_json={
                            "code": other_code,
                            "version": 2,
                            "nodes": [{"key": "later_review", "role": "DATA_ADMIN"}],
                        },
                        active=True,
                    )
                )
                await session.commit()
            with pytest.raises(RuntimeError, match="retroactive v1"):
                await seed_module.seed_demo_workflows(settings, factory)
        finally:
            async with factory() as session:
                await session.execute(
                    delete(WorkflowDefinition).where(
                        WorkflowDefinition.code.in_([code, other_code])
                    )
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
