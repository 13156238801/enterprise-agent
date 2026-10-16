"""提供审批申请提交所需的事务内 ORM 访问。"""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.approval.enums import ApprovalAction, ApprovalStatus
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

    async def get_application_for_read(self, approval_id: UUID) -> ApprovalApplication | None:
        """加载详情查询所需的申请及主数据变更关系。"""
        result = await self._session.execute(
            select(ApprovalApplication)
            .options(selectinload(ApprovalApplication.master_data_change))
            .where(ApprovalApplication.id == approval_id)
        )
        return result.scalar_one_or_none()

    async def list_by_applicant(
        self,
        applicant_id: str,
        *,
        status: ApprovalStatus | None,
        limit: int,
        offset: int,
    ) -> list[ApprovalApplication]:
        """按申请人和可选状态查询稳定排序的 limit-plus-one 页面。"""
        statement = (
            select(ApprovalApplication)
            .options(selectinload(ApprovalApplication.master_data_change))
            .where(ApprovalApplication.applicant_id == applicant_id)
            .order_by(ApprovalApplication.created_at.desc(), ApprovalApplication.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        if status is not None:
            statement = statement.where(ApprovalApplication.status == status)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_in_approval_candidates(self) -> list[ApprovalApplication]:
        """批量读取所有审批中申请及变更关系供 Service 校验和过滤。"""
        result = await self._session.execute(
            select(ApprovalApplication)
            .options(selectinload(ApprovalApplication.master_data_change))
            .where(ApprovalApplication.status == ApprovalStatus.IN_APPROVAL)
            .order_by(
                ApprovalApplication.submitted_at.asc(),
                ApprovalApplication.created_at.asc(),
                ApprovalApplication.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def list_task_creation_records(self, task_ids: list[str]) -> dict[str, ApprovalRecord]:
        """一次查询多个 task 创建记录，并为重复目标保留最新记录。"""
        if not task_ids:
            return {}
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.target_task_id.in_(task_ids))
            .order_by(
                ApprovalRecord.target_task_id,
                ApprovalRecord.created_at.desc(),
                ApprovalRecord.id.desc(),
            )
        )
        records: dict[str, ApprovalRecord] = {}
        for record in result.scalars().all():
            if record.target_task_id is not None:
                records.setdefault(record.target_task_id, record)
        return records

    async def list_records(self, approval_id: UUID) -> list[ApprovalRecord]:
        """按创建时间与 ID 正序读取申请的 append-only 历史。"""
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(ApprovalRecord.approval_id == approval_id)
            .order_by(ApprovalRecord.created_at.asc(), ApprovalRecord.id.asc())
        )
        return list(result.scalars().all())

    async def find_latest_submit_record(self, approval_id: UUID) -> ApprovalRecord | None:
        """按时间和 ID 倒序查找最近一次提交以界定当前审批周期。"""
        result = await self._session.execute(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.approval_id == approval_id,
                ApprovalRecord.action == ApprovalAction.SUBMIT,
            )
            .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_reviewer_participated_since(
        self,
        approval_id: UUID,
        actor_id: str,
        submit_record: ApprovalRecord,
    ) -> bool:
        """判断操作者是否在指定提交记录的稳定时间与 ID 边界后批准或驳回。"""
        after_submit = or_(
            ApprovalRecord.created_at > submit_record.created_at,
            and_(
                ApprovalRecord.created_at == submit_record.created_at,
                ApprovalRecord.id > submit_record.id,
            ),
        )
        result = await self._session.execute(
            select(ApprovalRecord.id)
            .where(
                ApprovalRecord.approval_id == approval_id,
                ApprovalRecord.operator_id == actor_id,
                ApprovalRecord.action.in_({ApprovalAction.APPROVE, ApprovalAction.REJECT}),
                after_submit,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

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
