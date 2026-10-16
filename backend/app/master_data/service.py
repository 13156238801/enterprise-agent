"""编排主数据读取和受 CAS、幂等及审计保护的草稿写事务。"""

import json
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
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
from app.idempotency.service import (
    IdempotencyService,
    canonical_request_hash,
    normalize_idempotency_key,
)
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
    """协调可信身份、主数据规则、审批状态机和原子写入事务。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求 Session，并构造不拥有事务的持久化组件。"""
        self._session = session
        self._repository = MasterDataRepository(session)
        self._idempotency = IdempotencyService(session)
        self._audit = AuditRepository(session)

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
        idempotency_key: object = None,
    ) -> ChangeDraftResponse:
        """校验规范化请求并在同一事务中创建或更新幂等草稿。"""
        _validate_identity(entity_type, entity_key)
        changes = _validated_changes(payload.changes)
        reason = _validated_reason(payload.reason)
        key = normalize_idempotency_key(idempotency_key)
        if payload.draft_approval_id is None:
            if payload.expected_version is not None:
                raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE)
            return await self._create_draft(
                entity_type, entity_key, changes, reason, auth_context, key
            )
        expected_version = _validate_draft_expected_version(payload.expected_version)
        return await self._update_draft(
            entity_type,
            entity_key,
            payload.draft_approval_id,
            expected_version,
            changes,
            reason,
            auth_context,
            key,
        )

    async def _create_draft(
        self,
        entity_type: str,
        entity_key: str,
        changes: dict[str, JsonValue],
        reason: str,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> ChangeDraftResponse:
        """以原子幂等 claim 创建申请、变更快照、CREATE 历史和审计。"""
        resource_id = json.dumps(
            {"entity_type": entity_type, "entity_key": entity_key},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = canonical_request_hash(
            {
                "draft_approval_id": None,
                "expected_version": None,
                "entity_type": entity_type,
                "entity_key": entity_key,
                "changes": changes,
                "reason": reason,
            }
        )
        actor_id = str(auth_context.user_id)
        async with self._session.begin():
            claim = await self._idempotency.claim(
                actor_id=actor_id,
                key=idempotency_key,
                action="MASTER_DATA_DRAFT_CREATE",
                resource_type="master_data_entity",
                resource_id=resource_id,
                request_hash=request_hash,
            )
            if not claim.is_new:
                return self._idempotency.replay(claim, ChangeDraftResponse)

            approval_id = uuid4()
            change_id = uuid4()
            entity = await self._repository.get_entity(entity_type, entity_key)
            if entity is None:
                raise MasterDataError(MasterDataErrorCode.NOT_FOUND)
            before = _apply_domain(lambda: validate_json_object(entity.data_json, "data_json"))
            after = _apply_domain(lambda: apply_master_data_patch(before, changes))
            diff = _apply_domain(lambda: build_master_data_diff(before, after))
            _reject_no_change(diff)

            approval = ApprovalApplication(
                id=approval_id,
                business_type=BusinessType.MASTER_DATA_CHANGE,
                business_id=str(change_id),
                applicant_id=actor_id,
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
                operator_id=actor_id,
                action=ApprovalAction.CREATE,
                from_status=None,
                to_status=ApprovalStatus.DRAFT,
            )
            await self._repository.add_draft(approval, change, record)
            response = _draft_response(approval, change, diff, approval_version=1)
            await self._audit.append(
                actor_id=actor_id,
                action="MASTER_DATA_DRAFT_CREATE",
                resource_type="approval",
                resource_id=str(approval_id),
                metadata_json={
                    "approval_id": str(approval_id),
                    "change_id": str(change_id),
                    "entity_type": entity_type,
                    "entity_key": entity_key,
                    "from_version": None,
                    "to_version": 1,
                    "from_status": None,
                    "to_status": ApprovalStatus.DRAFT.value,
                },
            )
            await self._idempotency.complete(claim, response)
        return response

    async def _update_draft(
        self,
        entity_type: str,
        entity_key: str,
        approval_id: UUID,
        expected_version: int,
        changes: dict[str, JsonValue],
        reason: str,
        auth_context: AuthContext,
        idempotency_key: str,
    ) -> ChangeDraftResponse:
        """先通过 Approval CAS，再 flush 草稿、SAVE 历史、审计和重放结果。"""
        actor_id = str(auth_context.user_id)
        request_hash = canonical_request_hash(
            {
                "draft_approval_id": str(approval_id),
                "expected_version": expected_version,
                "entity_type": entity_type,
                "entity_key": entity_key,
                "changes": changes,
                "reason": reason,
            }
        )
        async with self._session.begin():
            claim = await self._idempotency.claim(
                actor_id=actor_id,
                key=idempotency_key,
                action="MASTER_DATA_DRAFT_SAVE",
                resource_type="approval",
                resource_id=str(approval_id),
                request_hash=request_hash,
            )
            if not claim.is_new:
                return self._idempotency.replay(claim, ChangeDraftResponse)

            approval = await self._repository.get_draft_by_approval_id(approval_id)
            if approval is None or approval.business_type is not BusinessType.MASTER_DATA_CHANGE:
                raise MasterDataError(MasterDataErrorCode.DRAFT_NOT_FOUND)
            change = approval.master_data_change
            if change is None or approval.business_id != str(change.id):
                raise MasterDataError(MasterDataErrorCode.DRAFT_NOT_FOUND)

            try:
                decision = decide_transition(
                    current_status=approval.status,
                    action=ApprovalAction.SAVE,
                    context=TransitionContext(
                        actor_id=actor_id, applicant_id=approval.applicant_id
                    ),
                )
            except ApprovalTransitionError as error:
                _raise_transition_error(error)
            if (change.entity_type, change.entity_key) != (entity_type, entity_key):
                raise MasterDataError(MasterDataErrorCode.DRAFT_ENTITY_MISMATCH)
            entity = await self._repository.get_entity(entity_type, entity_key)
            if entity is None or entity.version != change.base_entity_version:
                raise MasterDataError(MasterDataErrorCode.VERSION_CONFLICT)
            if approval.version != expected_version:
                raise MasterDataError(MasterDataErrorCode.VERSION_CONFLICT)

            after = _apply_domain(lambda: apply_master_data_patch(change.form_data, changes))
            if after == change.form_data:
                raise MasterDataError(MasterDataErrorCode.NO_CHANGE)
            before = _apply_domain(lambda: validate_json_object(change.before_data, "before_data"))
            diff = _apply_domain(lambda: build_master_data_diff(before, after))
            _reject_no_change(diff)

            new_version = await self._approval_version_cas(approval_id, expected_version)
            change.form_data = after
            change.change_items = [_diff_item_dict(item) for item in diff]
            change.change_reason = reason
            record = ApprovalRecord(
                approval_id=approval.id,
                operator_id=actor_id,
                action=decision.action,
                from_status=decision.from_status,
                to_status=decision.to_status,
            )
            await self._repository.save_draft(change, record)
            response = _draft_response(approval, change, diff, approval_version=new_version)
            await self._audit.append(
                actor_id=actor_id,
                action="MASTER_DATA_DRAFT_SAVE",
                resource_type="approval",
                resource_id=str(approval.id),
                metadata_json={
                    "approval_id": str(approval.id),
                    "change_id": str(change.id),
                    "entity_type": entity_type,
                    "entity_key": entity_key,
                    "from_version": expected_version,
                    "to_version": new_version,
                    "from_status": decision.from_status.value,
                    "to_status": decision.to_status.value,
                },
            )
            await self._idempotency.complete(claim, response)
        return response

    async def _approval_version_cas(self, approval_id: UUID, expected_version: int) -> int:
        """执行草稿 SAVE 的数据库 CAS，并映射未命中版本冲突。"""
        from app.approval.repository import ApprovalRepository

        version = await ApprovalRepository(self._session).update_application_if_version(
            approval_id, expected_version, {}
        )
        if version is None:
            raise MasterDataError(MasterDataErrorCode.VERSION_CONFLICT)
        return version


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
    """验证 patch 为非空 JSON-compatible object 并统一转换应用错误。"""
    try:
        validated = validate_json_object(changes, "changes")
    except MasterDataDomainError as error:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE) from error
    if not validated:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE)
    return validated


def _validate_draft_expected_version(value: object) -> int:
    """严格校验草稿更新版本，拒绝 bool、非整数和非正值。"""
    if type(value) is not int or value <= 0:
        raise MasterDataError(MasterDataErrorCode.INVALID_CHANGE)
    return value


def _diff_item_dict(item: MasterDataDiffItem) -> dict[str, object]:
    """将领域 Diff item 转为独立 JSONB 字典。"""
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
    *,
    approval_version: int,
) -> ChangeDraftResponse:
    """从草稿及数据库确认的新版本构造结构化响应。"""
    return ChangeDraftResponse(
        approval_id=approval.id,
        approval_version=approval_version,
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
