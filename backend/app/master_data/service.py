"""编排主数据读取和 Approval DRAFT 创建、更新事务。"""

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.domain.approval.context import TransitionContext
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.approval.errors import ApprovalTransitionError, ApprovalTransitionErrorCode
from app.domain.approval.state_machine import decide_transition
from app.domain.master_data.diff import (
    JsonValue,
    MasterDataDiffItem,
    apply_master_data_patch,
    build_master_data_diff,
    validate_entity_identity,
    validate_json_object,
)
from app.domain.master_data.errors import MasterDataDomainError
from app.master_data.errors import MasterDataError, MasterDataErrorCode
from app.master_data.repository import MasterDataRepository
from app.master_data.schemas import (
    ChangeDraftRequest,
    ChangeDraftResponse,
    MasterDataDiffItemResponse,
    MasterDataEntityResponse,
)
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange


class MasterDataService:
    """协调可信身份、主数据规则、审批状态机和数据库事务。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求的 Session，并构造不拥有事务的 Repository。"""
        self._session = session
        self._repository = MasterDataRepository(session)

    async def get_entity(self, entity_type: str, entity_key: str) -> MasterDataEntityResponse:
        """验证实体身份并返回当前主数据快照，不修改持久化状态。"""
        _validate_identity(entity_type, entity_key)
        entity = await self._repository.get_entity(entity_type, entity_key)
        if entity is None:
            raise MasterDataError(MasterDataErrorCode.NOT_FOUND)
        return MasterDataEntityResponse(
            id=entity.id,
            entity_type=entity.entity_type,
            entity_key=entity.entity_key,
            data=entity.data_json,
            version=entity.version,
        )

    async def create_or_update_draft(
        self,
        entity_type: str,
        entity_key: str,
        payload: ChangeDraftRequest,
        auth_context: AuthContext,
    ) -> ChangeDraftResponse:
        """在事务中按 AuthContext 创建或更新主数据变更草稿。"""
        _validate_identity(entity_type, entity_key)
        changes = _validated_changes(payload.changes)
        reason = _validated_reason(payload.reason)
        if payload.draft_approval_id is None:
            return await self._create_draft(entity_type, entity_key, changes, reason, auth_context)
        return await self._update_draft(
            entity_type,
            entity_key,
            payload.draft_approval_id,
            changes,
            reason,
            auth_context,
        )

    async def _create_draft(
        self,
        entity_type: str,
        entity_key: str,
        changes: dict[str, JsonValue],
        reason: str,
        auth_context: AuthContext,
    ) -> ChangeDraftResponse:
        """以预生成 UUID 在单事务内创建申请、变更快照和 CREATE 历史。"""
        async with self._session.begin():
            # 预先确定相互引用的 ID，避免 business_id 与非空 change FK 形成插入顺序环。
            approval_id = uuid4()
            change_id = uuid4()
            entity = await self._repository.get_entity(entity_type, entity_key)
            if entity is None:
                raise MasterDataError(MasterDataErrorCode.NOT_FOUND)
            before = _apply_domain(lambda: validate_json_object(entity.data_json, "data_json"))
            after = _apply_domain(lambda: apply_master_data_patch(before, changes))
            diff = _apply_domain(lambda: build_master_data_diff(before, after))
            _reject_no_change(diff)

            # 草稿只记录拟议快照；真正审批完成前不能写回当前主数据。
            applicant_id = str(auth_context.user_id)
            approval = ApprovalApplication(
                id=approval_id,
                business_type=BusinessType.MASTER_DATA_CHANGE,
                business_id=str(change_id),
                applicant_id=applicant_id,
                status=ApprovalStatus.DRAFT,
            )
            change = MasterDataChange(
                id=change_id,
                approval_id=approval_id,
                entity_type=entity_type,
                entity_key=entity_key,
                before_data=before,
                form_data=after,
                change_items=[_diff_item_dict(item) for item in diff],
                change_reason=reason,
                base_entity_version=entity.version,
            )
            record = ApprovalRecord(
                approval_id=approval_id,
                operator_id=applicant_id,
                action=ApprovalAction.CREATE,
                from_status=None,
                to_status=ApprovalStatus.DRAFT,
            )
            await self._repository.add_draft(approval, change, record)
        return _draft_response(approval, change, diff)

    async def _update_draft(
        self,
        entity_type: str,
        entity_key: str,
        approval_id: UUID,
        changes: dict[str, JsonValue],
        reason: str,
        auth_context: AuthContext,
    ) -> ChangeDraftResponse:
        """按明确申请 ID 更新草稿并追加由状态机决策的 SAVE 历史。"""
        async with self._session.begin():
            approval = await self._repository.get_draft_by_approval_id(approval_id)
            if approval is None or approval.business_type is not BusinessType.MASTER_DATA_CHANGE:
                raise MasterDataError(MasterDataErrorCode.DRAFT_NOT_FOUND)
            change = approval.master_data_change
            if change is None:
                raise MasterDataError(MasterDataErrorCode.DRAFT_NOT_FOUND)

            # 使用调用方明确给出的申请 ID，避免猜测同一实体的“最近草稿”。
            try:
                decision = decide_transition(
                    current_status=approval.status,
                    action=ApprovalAction.SAVE,
                    context=TransitionContext(
                        actor_id=str(auth_context.user_id),
                        applicant_id=approval.applicant_id,
                    ),
                )
            except ApprovalTransitionError as error:
                _raise_transition_error(error)

            if (change.entity_type, change.entity_key) != (entity_type, entity_key):
                raise MasterDataError(MasterDataErrorCode.DRAFT_ENTITY_MISMATCH)

            # 新 patch 累加在现有 after；before 和基线版本固定，避免隐式 rebase。
            after = _apply_domain(lambda: apply_master_data_patch(change.form_data, changes))
            if after == change.form_data:
                raise MasterDataError(MasterDataErrorCode.NO_CHANGE)
            before = _apply_domain(lambda: validate_json_object(change.before_data, "before_data"))
            # 每次对原始 before 重算完整 Diff；并发版本冲突留给 P1-010 执行。
            diff = _apply_domain(lambda: build_master_data_diff(before, after))
            _reject_no_change(diff)

            change.form_data = after
            change.change_items = [_diff_item_dict(item) for item in diff]
            change.change_reason = reason
            record = ApprovalRecord(
                approval_id=approval.id,
                operator_id=str(auth_context.user_id),
                action=decision.action,
                from_status=decision.from_status,
                to_status=decision.to_status,
            )
            await self._repository.save_draft(change, record)
        return _draft_response(approval, change, diff)


def _validate_identity(entity_type: str, entity_key: str) -> None:
    """将领域实体身份错误转换为统一主数据应用错误。"""
    try:
        validate_entity_identity(entity_type, entity_key)
    except MasterDataDomainError as error:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE) from error


def _raise_transition_error(error: ApprovalTransitionError) -> None:
    """将 P1-002 SAVE 状态机错误映射为主数据草稿应用错误。"""
    if error.code is ApprovalTransitionErrorCode.INVALID_STATUS_ACTION:
        raise MasterDataError(MasterDataErrorCode.DRAFT_NOT_EDITABLE) from error
    if error.code is ApprovalTransitionErrorCode.ACTOR_NOT_APPLICANT:
        raise MasterDataError(MasterDataErrorCode.DRAFT_FORBIDDEN) from error
    raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE) from error


def _apply_domain(operation):
    """将领域 patch/diff 校验异常转换为稳定应用错误。"""
    try:
        return operation()
    except MasterDataDomainError as error:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE) from error


def _reject_no_change(diff: tuple[MasterDataDiffItem, ...]) -> None:
    """拒绝不产生字段差异的草稿创建或更新。"""
    if not diff:
        raise MasterDataError(MasterDataErrorCode.NO_CHANGE)


def _validated_reason(reason: object) -> str:
    """规范化并验证变更原因，限制为 1 至 2000 个字符。"""
    if not isinstance(reason, str):
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE)
    normalized = reason.strip()
    if not 1 <= len(normalized) <= 2000:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE)
    return normalized


def _validated_changes(changes: object) -> dict[str, JsonValue]:
    """验证 patch 为非空的 JSON-compatible object 并统一转换应用错误。"""
    try:
        validated = validate_json_object(changes, "changes")
    except MasterDataDomainError as error:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE) from error
    if not validated:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE)
    return validated


def _diff_item_dict(item: MasterDataDiffItem) -> dict:
    """将领域 Diff item 转换为独立 JSONB 字典。"""
    return {
        "field": item.field,
        "operation": item.operation.value,
        "before": item.before,
        "after": item.after,
    }


def _draft_response(
    approval: ApprovalApplication,
    change: MasterDataChange,
    diff: tuple[MasterDataDiffItem, ...],
) -> ChangeDraftResponse:
    """从已持久化申请和变更快照构造结构化草稿响应。"""
    return ChangeDraftResponse(
        approval_id=approval.id,
        change_id=change.id,
        status=approval.status,
        entity_type=change.entity_type,
        entity_key=change.entity_key,
        base_entity_version=change.base_entity_version,
        before=change.before_data,
        after=change.form_data,
        diff=[
            MasterDataDiffItemResponse(
                field=item.field,
                operation=item.operation,
                before=item.before,
                after=item.after,
            )
            for item in diff
        ],
        reason=change.change_reason or "",
    )
