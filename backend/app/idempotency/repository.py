"""执行 PostgreSQL 原子幂等 claim、重放读取和结果完成。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.idempotency.errors import IdempotencyError, IdempotencyErrorCode
from app.models.governance import IdempotencyRecord


class IdempotencyRepository:
    """使用调用方事务操作幂等记录，不提交或回滚 Session。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前写操作的 AsyncSession。"""
        self._session = session

    async def claim(
        self,
        *,
        actor_id: str,
        key: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_hash: str,
    ) -> tuple[IdempotencyRecord, bool]:
        """以唯一索引原子创建或重领过期键，并返回是否由本事务持有。"""
        now = datetime.now(UTC)
        values = {
            "id": uuid4(),
            "actor_id": actor_id,
            "key": key,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "request_hash": request_hash,
            "response_json": None,
            "expires_at": now + timedelta(hours=24),
            "created_at": now,
            "updated_at": now,
        }
        statement = (
            insert(IdempotencyRecord)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_idempotency_records_actor_key",
                set_={
                    "id": values["id"],
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "request_hash": request_hash,
                    "response_json": None,
                    "expires_at": values["expires_at"],
                    "created_at": now,
                    "updated_at": now,
                },
                where=IdempotencyRecord.expires_at <= func.statement_timestamp(),
            )
            .returning(IdempotencyRecord)
        )
        result = await self._session.execute(statement)
        record = result.scalar_one_or_none()
        if record is not None:
            return record, True

        result = await self._session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.key == key,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise IdempotencyError(IdempotencyErrorCode.DATA_INTEGRITY_ERROR)
        return record, False

    async def complete(self, record_id: UUID, response_json: dict[str, object]) -> bool:
        """仅将本事务持有且尚未完成的 claim 写入安全 JSON 响应。"""
        result = await self._session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.response_json.is_(None),
            )
            .values(response_json=response_json, updated_at=func.now())
            .returning(IdempotencyRecord.id)
        )
        return result.scalar_one_or_none() is not None
