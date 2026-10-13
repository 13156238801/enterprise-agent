"""提供工作流定义查询与新增，不拥有事务提交或回滚。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.workflow.definition import (
    WorkflowDefinitionSpec,
    parse_workflow_definition,
    workflow_definition_to_dict,
)
from app.domain.workflow.errors import WorkflowDefinitionError, WorkflowDefinitionErrorCode
from app.models.workflow import WorkflowDefinition


class WorkflowDefinitionRepository:
    """使用调用方的 AsyncSession 持久化和读取不可变工作流版本。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定调用方管理的单事务 Session。"""
        self._session = session

    async def get_by_code_version(self, code: str, version: int) -> WorkflowDefinitionSpec | None:
        """按稳定业务键读取版本，并验证 JSON 快照与索引列一致。"""
        result = await self._session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.code == code,
                WorkflowDefinition.version == version,
            )
        )
        row = result.scalar_one_or_none()
        return self._to_spec(row) if row is not None else None

    async def get_active(self, code: str) -> WorkflowDefinitionSpec | None:
        """读取指定流程唯一 active 版本，没有 active 版本时返回 None。"""
        result = await self._session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.code == code,
                WorkflowDefinition.active.is_(True),
            )
        )
        row = result.scalar_one_or_none()
        return self._to_spec(row) if row is not None else None

    async def list_versions(self, code: str) -> list[WorkflowDefinitionSpec]:
        """按版本升序读取流程历史并逐条验证快照一致性。"""
        result = await self._session.execute(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.code == code)
            .order_by(WorkflowDefinition.version)
        )
        return [self._to_spec(row) for row in result.scalars().all()]

    async def add(
        self, spec: WorkflowDefinitionSpec, *, active: bool = False
    ) -> WorkflowDefinition:
        """从单一领域 Spec 构造 row 并 flush，但不提交事务。"""
        row = WorkflowDefinition(
            code=spec.code,
            version=spec.version,
            definition_json=workflow_definition_to_dict(spec),
            active=active,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def activate_if_none(self, code: str, version: int) -> bool:
        """仅在流程当前没有 active 版本时启用指定版本并立即 flush。"""
        active_result = await self._session.execute(
            select(WorkflowDefinition.id).where(
                WorkflowDefinition.code == code,
                WorkflowDefinition.active.is_(True),
            )
        )
        if active_result.scalar_one_or_none() is not None:
            # Seed 只能恢复空 active 状态，不能把新版本已生效的流程降回旧版。
            return False
        result = await self._session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.code == code,
                WorkflowDefinition.version == version,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        row.active = True
        await self._session.flush()
        return True

    @staticmethod
    def _to_spec(row: WorkflowDefinition) -> WorkflowDefinitionSpec:
        """解析持久化快照并拒绝与可索引列不一致的数据。"""
        spec = parse_workflow_definition(row.definition_json)
        if spec.code != row.code or spec.version != row.version:
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.PAYLOAD_INVALID,
                "流程 JSON code/version 与数据库索引列不一致。",
            )
        return spec
