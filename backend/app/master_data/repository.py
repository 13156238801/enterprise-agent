"""提供主数据实体与审批草稿的 AsyncSession 持久化操作。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange, MasterDataEntity


class MasterDataRepository:
    """使用调用方 Session 查询并 flush 主数据，不持有事务控制权。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前 Service 操作独享的 AsyncSession。"""
        self._session = session

    async def get_entity(self, entity_type: str, entity_key: str) -> MasterDataEntity | None:
        """按稳定业务身份读取当前主数据实体。"""
        result = await self._session.execute(
            select(MasterDataEntity).where(
                MasterDataEntity.entity_type == entity_type,
                MasterDataEntity.entity_key == entity_key,
            )
        )
        return result.scalar_one_or_none()

    async def add_entity(self, entity: MasterDataEntity) -> None:
        """加入并 flush 新实体以检查约束，但不提交事务。"""
        self._session.add(entity)
        await self._session.flush()

    async def get_draft_by_approval_id(self, approval_id: UUID) -> ApprovalApplication | None:
        """加载审批申请和关联变更，避免访问未加载的 Async ORM 关系。"""
        result = await self._session.execute(
            select(ApprovalApplication)
            .options(selectinload(ApprovalApplication.master_data_change))
            .where(ApprovalApplication.id == approval_id)
        )
        return result.scalar_one_or_none()

    async def add_draft(
        self,
        approval: ApprovalApplication,
        change: MasterDataChange,
        record: ApprovalRecord,
    ) -> None:
        """将申请、变更和 CREATE 历史作为一组 flush，不提交事务。"""
        self._session.add_all([approval, change, record])
        await self._session.flush()

    async def save_draft(self, change: MasterDataChange, record: ApprovalRecord) -> None:
        """flush 已更新变更和新增 SAVE 历史，不提交事务。"""
        self._session.add(record)
        await self._session.flush()
