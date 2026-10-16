"""为既有集成回归测试提供显式 P1-010 写请求参数。"""

from uuid import uuid4

from sqlalchemy import select

from app.models.approval import ApprovalApplication


async def approval_write(service, action: str, *args, **kwargs):
    """读取当前版本并为旧领域场景调用补入唯一幂等键。"""
    approval_id = args[0]
    async with service._session.bind.connect() as connection:
        expected_version = await connection.scalar(
            select(ApprovalApplication.version).where(ApprovalApplication.id == approval_id)
        )
    if expected_version is None:
        expected_version = 1
    actor_and_payload = args[1:]
    if action == "submit":
        return await service.submit(
            approval_id,
            *actor_and_payload,
            expected_version=kwargs.pop("expected_version", expected_version),
            idempotency_key=kwargs.pop("idempotency_key", f"regression-{uuid4()}"),
            **kwargs,
        )
    if action == "approve":
        auth_context, *remaining = actor_and_payload
        comment = kwargs.pop("comment", remaining[0] if remaining else None)
        return await service.approve(
            approval_id,
            auth_context,
            expected_version=kwargs.pop("expected_version", expected_version),
            comment=comment,
            idempotency_key=kwargs.pop("idempotency_key", f"regression-{uuid4()}"),
            **kwargs,
        )
    if action == "reject":
        auth_context, target, comment, *remaining = actor_and_payload
        expected = remaining[0] if remaining else expected_version
        return await service.reject(
            approval_id,
            auth_context,
            target,
            comment,
            expected_version=kwargs.pop("expected_version", expected),
            idempotency_key=kwargs.pop("idempotency_key", f"regression-{uuid4()}"),
            **kwargs,
        )
    if action == "withdraw":
        auth_context, *remaining = actor_and_payload
        comment = kwargs.pop("comment", remaining[0] if remaining else None)
        return await service.withdraw(
            approval_id,
            auth_context,
            expected_version=kwargs.pop("expected_version", expected_version),
            comment=comment,
            idempotency_key=kwargs.pop("idempotency_key", f"regression-{uuid4()}"),
            **kwargs,
        )
    raise AssertionError(f"Unsupported approval action in test helper: {action}")


async def draft_write(service, entity_type, entity_key, payload, auth_context):
    """为草稿回归场景补入数据库当前版本和独立幂等键。"""
    expected_version = None
    if payload.draft_approval_id is not None:
        async with service._session.bind.connect() as connection:
            expected_version = await connection.scalar(
                select(ApprovalApplication.version).where(
                    ApprovalApplication.id == payload.draft_approval_id
                )
            )
        if expected_version is None:
            expected_version = 1
    if payload.expected_version is None and payload.draft_approval_id is not None:
        payload = payload.model_copy(update={"expected_version": expected_version})
    return await service.create_or_update_draft(
        entity_type,
        entity_key,
        payload,
        auth_context,
        idempotency_key=f"regression-{uuid4()}",
    )
