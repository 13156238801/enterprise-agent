"""验证 P1-005 upgrade backfill 并清理 legacy migration fixture。"""

import asyncio
from uuid import UUID

from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine

_APPROVAL_ID = UUID("00000000-0000-0000-0000-000000005005")
_CHANGE_ID = UUID("00000000-0000-0000-0000-000000005006")
_LEGACY_DATA = {"legacy_field": "preserved", "count": 3}


async def main() -> None:
    """确认 entity key 和两项 backfill 正确，随后删除本脚本样本行。"""
    engine = create_db_engine(get_settings())
    try:
        async with engine.begin() as connection:
            result = await connection.execute(
                text(
                    "SELECT entity_key, before_data, form_data, base_entity_version "
                    "FROM master_data_change WHERE id = :id"
                ),
                {"id": _CHANGE_ID},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise RuntimeError("P1-005 migration fixture is missing after upgrade")
            if (
                row["entity_key"] != "legacy-supplier-001"
                or row["before_data"] != _LEGACY_DATA
                or row["form_data"] != _LEGACY_DATA
                or row["base_entity_version"] != 1
            ):
                raise RuntimeError(
                    "P1-005 legacy snapshot backfill did not preserve expected values"
                )
            await connection.execute(
                text("DELETE FROM master_data_change WHERE id = :id"), {"id": _CHANGE_ID}
            )
            await connection.execute(
                text("DELETE FROM approval_application WHERE id = :id"),
                {"id": _APPROVAL_ID},
            )
    finally:
        await engine.dispose()
    print("P1-005 entity rename and before/version backfill verified.")


if __name__ == "__main__":
    asyncio.run(main())
