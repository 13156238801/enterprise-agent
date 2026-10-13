"""Workflow Definition 的真实 PostgreSQL Repository 与约束测试。"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import WorkflowDefinitionSpec, WorkflowNodeDefinition
from app.infrastructure.database import create_db_engine, create_session_factory
from app.models.workflow import WorkflowDefinition
from app.workflow.repository import WorkflowDefinitionRepository


def test_postgres_workflow_constraints_json_roundtrip_and_repository_transaction() -> None:
    """验证真实 PostgreSQL 约束、JSONB 快照、导航映射和 Repository 不提交事务。"""
    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    suffix = uuid4().hex
    code = f"test_workflow_{suffix[:16]}"
    other_code = f"test_other_{suffix[:16]}"
    spec_v1 = WorkflowDefinitionSpec(
        code=code,
        version=1,
        nodes=(WorkflowNodeDefinition("manager_review", UserRole.APPROVER),),
    )
    spec_v2 = WorkflowDefinitionSpec(
        code=code,
        version=2,
        nodes=(WorkflowNodeDefinition("manager_review", UserRole.APPROVER),),
    )
    different_code_spec = WorkflowDefinitionSpec(
        code=other_code,
        version=1,
        nodes=(WorkflowNodeDefinition("admin_review", UserRole.DATA_ADMIN),),
    )

    async def run() -> None:
        """执行约束断言并删除本测试创建的工作流记录。"""
        try:
            async with factory() as session:
                repository = WorkflowDefinitionRepository(session)
                await repository.add(spec_v1)
                await session.rollback()
            async with factory() as session:
                assert (
                    await session.scalar(
                        select(WorkflowDefinition.id).where(WorkflowDefinition.code == code)
                    )
                    is None
                )

            async with factory() as session:
                repository = WorkflowDefinitionRepository(session)
                row_v1 = await repository.add(spec_v1)
                await repository.add(spec_v2)
                row_other = await repository.add(different_code_spec, active=True)
                v1_id = row_v1.id
                other_id = row_other.id
                await session.commit()

            async with factory() as session:
                repository = WorkflowDefinitionRepository(session)
                assert await repository.get_by_code_version(code, 1) == spec_v1
                assert await repository.get_by_code_version(code, 3) is None
                assert await repository.get_active(code) is None
                assert await repository.get_active(other_code) == different_code_spec
                assert await repository.list_versions(code) == [spec_v1, spec_v2]
                row = await session.get(WorkflowDefinition, v1_id)
                assert row is not None
                assert row.definition_json == {
                    "code": code,
                    "version": 1,
                    "nodes": [{"key": "manager_review", "role": "APPROVER"}],
                }
                assert row.created_at.tzinfo is not None
                assert row.updated_at.tzinfo is not None

            async with factory() as session:
                with pytest.raises(IntegrityError):
                    await session.execute(
                        insert(WorkflowDefinition).values(
                            id=uuid4(),
                            code=f"invalid_version_{suffix[:12]}",
                            version=0,
                            definition_json={"code": "invalid_version", "version": 0, "nodes": []},
                            active=False,
                        )
                    )
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                with pytest.raises(IntegrityError):
                    session.add(
                        WorkflowDefinition(
                            code=code,
                            version=1,
                            definition_json={
                                "code": code,
                                "version": 1,
                                "nodes": [{"key": "manager_review", "role": "APPROVER"}],
                            },
                        )
                    )
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                row = await session.get(WorkflowDefinition, v1_id)
                assert row is not None
                row.active = True
                await session.commit()
            async with factory() as session:
                row = await session.scalar(
                    select(WorkflowDefinition).where(
                        WorkflowDefinition.code == code,
                        WorkflowDefinition.version == 2,
                    )
                )
                assert row is not None
                row.active = True
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

            async with factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(WorkflowDefinition).where(WorkflowDefinition.code == code)
                        )
                    ).all()
                )
                for row in rows:
                    row.active = False
                await session.flush()
                row = await session.get(WorkflowDefinition, other_id)
                assert row is not None
                await session.execute(
                    delete(WorkflowDefinition).where(
                        WorkflowDefinition.code.in_([code, other_code])
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())
