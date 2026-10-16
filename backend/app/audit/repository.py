"""将经过业务白名单构造的审计事实追加到当前事务。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import AuditLog


class AuditRepository:
    """只提供审计追加接口，不暴露更新、删除或事务控制方法。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定业务写事务使用的 AsyncSession。"""
        self._session = session

    async def append(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata_json: dict[str, object],
    ) -> AuditLog:
        """加入并 flush 一条审计事实，提交或回滚由调用方事务决定。"""
        record = AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata_json,
        )
        self._session.add(record)
        await self._session.flush()
        return record
