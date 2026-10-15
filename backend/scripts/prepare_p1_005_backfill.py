"""验证 P1-005 downgrade 后旧 schema 并准备迁移 backfill 样本。"""

import asyncio
import json
from uuid import UUID

from sqlalchemy import inspect, text

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine

_APPROVAL_ID = UUID("00000000-0000-0000-0000-000000005005")
_CHANGE_ID = UUID("00000000-0000-0000-0000-000000005006")
_LEGACY_DATA = {"legacy_field": "preserved", "count": 3}
_REQUIRED_TABLES = {
    "approval_application",
    "approval_record",
    "master_data_change",
    "users",
    "user_roles",
    "auth_sessions",
    "workflow_definitions",
}


def _verify_legacy_schema(sync_connection) -> None:
    """确认 downgrade 恢复 entity_id 并保留 P1-001 至 P1-004 表。"""
    inspector = inspect(sync_connection)
    tables = set(inspector.get_table_names())
    if not _REQUIRED_TABLES <= tables:
        raise RuntimeError(f"P1-005 downgrade removed required tables: {_REQUIRED_TABLES - tables}")
    if "master_data_entities" in tables:
        raise RuntimeError("master_data_entities still exists after P1-005 downgrade")
    columns = {column["name"] for column in inspector.get_columns("master_data_change")}
    if "entity_id" not in columns or {"entity_key", "before_data", "base_entity_version"} & columns:
        raise RuntimeError("master_data_change did not return to its legacy P1-004 schema")


async def main() -> None:
    """检查 downgrade schema 后插入一条旧版变更行供升级 backfill。"""
    engine = create_db_engine(get_settings())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_verify_legacy_schema)
            await connection.execute(
                text(
                    "INSERT INTO approval_application "
                    "(id, business_type, business_id, applicant_id, status, version) "
                    "VALUES (:id, 'MASTER_DATA_CHANGE', :business_id, :applicant_id, 'DRAFT', 1)"
                ),
                {
                    "id": _APPROVAL_ID,
                    "business_id": str(_CHANGE_ID),
                    "applicant_id": "p1-005-migration-fixture",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO master_data_change "
                    "(id, approval_id, entity_type, entity_id, form_data, change_items, "
                    "change_reason) "
                    "VALUES (:id, :approval_id, 'supplier', 'legacy-supplier-001', "
                    "CAST(:form_data AS jsonb), CAST(:change_items AS jsonb), 'legacy fixture')"
                ),
                {
                    "id": _CHANGE_ID,
                    "approval_id": _APPROVAL_ID,
                    "form_data": json.dumps(_LEGACY_DATA),
                    "change_items": "[]",
                },
            )
    finally:
        await engine.dispose()
    print("P1-005 downgrade restored entity_id and legacy fixture is ready.")


if __name__ == "__main__":
    asyncio.run(main())
