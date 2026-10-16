"""实现幂等键规范化、稳定指纹和事务内结果重放。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.idempotency.errors import IdempotencyError, IdempotencyErrorCode
from app.idempotency.repository import IdempotencyRepository
from app.models.governance import IdempotencyRecord

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class IdempotencyClaim:
    """表示当前请求是否取得新 claim 或应重放已提交结果。"""

    record: IdempotencyRecord
    is_new: bool


def normalize_idempotency_key(value: object) -> str:
    """去除幂等键首尾空白并验证 1 到 200 字符范围。"""
    if value is None:
        raise IdempotencyError(IdempotencyErrorCode.KEY_REQUIRED)
    if not isinstance(value, str):
        raise IdempotencyError(IdempotencyErrorCode.KEY_INVALID)
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise IdempotencyError(IdempotencyErrorCode.KEY_INVALID)
    return normalized


def canonical_request_hash(payload: Any) -> str:
    """对 JSON-compatible 语义 payload 做稳定序列化并返回 SHA-256 hex。"""
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class IdempotencyService:
    """协调 PostgreSQL claim、绑定冲突、重放与成功响应完成。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定调用方事务的 Session，不建立独立事务。"""
        self._repository = IdempotencyRepository(session)

    async def claim(
        self,
        *,
        actor_id: str,
        key: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_hash: str,
    ) -> IdempotencyClaim:
        """原子领取 key；现存绑定不同则冲突，已完成绑定则返回 replay。"""
        normalized_key = normalize_idempotency_key(key)
        record, is_new = await self._repository.claim(
            actor_id=actor_id,
            key=normalized_key,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_hash=request_hash,
        )
        if is_new:
            return IdempotencyClaim(record=record, is_new=True)
        binding_matches = (
            record.action == action
            and record.resource_type == resource_type
            and record.resource_id == resource_id
            and record.request_hash == request_hash
        )
        if not binding_matches:
            raise IdempotencyError(IdempotencyErrorCode.CONFLICT)
        if record.response_json is None:
            raise IdempotencyError(IdempotencyErrorCode.DATA_INTEGRITY_ERROR)
        return IdempotencyClaim(record=record, is_new=False)

    async def complete(self, claim: IdempotencyClaim, response: BaseModel) -> None:
        """将安全 Pydantic 响应写入当前 claim 并要求恰好更新一行。"""
        if not claim.is_new:
            raise IdempotencyError(IdempotencyErrorCode.DATA_INTEGRITY_ERROR)
        completed = await self._repository.complete(
            claim.record.id,
            response.model_dump(mode="json"),
        )
        if not completed:
            raise IdempotencyError(IdempotencyErrorCode.DATA_INTEGRITY_ERROR)

    @staticmethod
    def replay(claim: IdempotencyClaim, response_model: type[T]) -> T:
        """按目标响应模型解析已持久化结果，拒绝损坏的 replay payload。"""
        if claim.is_new or claim.record.response_json is None:
            raise IdempotencyError(IdempotencyErrorCode.DATA_INTEGRITY_ERROR)
        try:
            return response_model.model_validate(claim.record.response_json)
        except Exception as error:
            raise IdempotencyError(IdempotencyErrorCode.DATA_INTEGRITY_ERROR) from error
