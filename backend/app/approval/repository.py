"""提供审批申请提交所需的事务内 ORM 访问。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.approval.enums import ApprovalAction
from app.models.approval import ApprovalApplication, ApprovalRecord


class ApprovalRepository:
    """使用调用方提供的 AsyncSession 读取申请并追加历史，不管理事务。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定单请求事务使用的数据库会话。"""
        self._session = session

    async def get_application_for_action(self, approval_id: UUID) -> ApprovalApplication | None:
        """加载审批申请及主数据变更关系，避免异步隐式 lazy loading。"""
        result = await self._session.execute(
            select(ApprovalApplication)
            .options(selectinload(ApprovalApplication.master_data_change))
            .where(ApprovalApplication.id == approval_id)
        )
        return result.scalar_one_or_none()

    async def save_transition(self, record: ApprovalRecord) -> None:
        """追加状态变更记录并 flush，提交或回滚由 Service 事务负责。"""
        self._session.add(record)
        await self._session.flush()

    async def get_task_creation_record(self, task_id: str) -> ApprovalRecord | None:
        """读取创建指定 task 的最新历史，用于解析定向处理人。"""
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.target_task_id == task_id)
            .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_previous_node_approver(
        self, approval_id: UUID, previous_node_code: str, current_node_code: str
    ) -> ApprovalRecord | None:
        """查找最近一次从上一节点批准并进入当前节点的历史记录。"""
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.approval_id == approval_id,
                ApprovalRecord.action == ApprovalAction.APPROVE,
                ApprovalRecord.from_node_code == previous_node_code,
                ApprovalRecord.target_node_code == current_node_code,
            )
            .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
