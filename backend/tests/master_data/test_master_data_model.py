"""主数据 ORM metadata 与 schema contract 测试。"""

from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database import Base
from app.models.approval import ApprovalApplication
from app.models.master_data import MasterDataChange, MasterDataEntity


def test_master_data_entity_metadata_has_snapshot_constraints() -> None:
    """验证当前主数据 ORM 的 JSONB、默认版本、时区字段和约束。"""
    table = MasterDataEntity.__table__

    assert table.name == "master_data_entities"
    assert table.c.id.primary_key
    assert table.c.entity_type.type.length == 128
    assert table.c.entity_key.type.length == 128
    assert isinstance(table.c.data_json.type, JSONB)
    assert not table.c.data_json.nullable
    assert table.c.version.default.arg == 1
    assert table.c.version.server_default.arg.text == "1"
    assert any(
        isinstance(item, UniqueConstraint)
        and item.name == "uq_master_data_entities_type_key"
        and {column.name for column in item.columns} == {"entity_type", "entity_key"}
        for item in table.constraints
    )
    assert any(
        isinstance(item, CheckConstraint)
        and item.name == "ck_master_data_entities_version_positive"
        and "version > 0" in str(item.sqltext)
        for item in table.constraints
    )
    for name in ("created_at", "updated_at"):
        assert isinstance(table.c[name].type, DateTime)
        assert table.c[name].type.timezone


def test_master_data_change_metadata_uses_snapshot_evolution_fields() -> None:
    """验证 MasterDataChange 以 entity_key、before 和基线版本保存草稿快照。"""
    table = MasterDataChange.__table__

    assert "entity_key" in table.c
    assert "entity_id" not in table.c
    assert isinstance(table.c.before_data.type, JSONB)
    assert not table.c.before_data.nullable
    assert table.c.base_entity_version.type.python_type is int
    assert not table.c.base_entity_version.nullable
    assert any(
        isinstance(item, CheckConstraint)
        and item.name == "ck_master_data_change_base_version_positive"
        and "base_entity_version > 0" in str(item.sqltext)
        for item in table.constraints
    )
    assert {"form_data", "change_items", "ext_data"} <= set(table.c.keys())


def test_master_data_entity_is_registered_in_shared_metadata() -> None:
    """验证 MasterDataEntity 注册到应用和 Alembic 共用 metadata。"""
    assert Base.metadata.tables["master_data_entities"] is MasterDataEntity.__table__
    assert ApprovalApplication.__table__ in Base.metadata.tables.values()
