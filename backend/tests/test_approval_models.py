from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database import Base
from app.models import (
    ApprovalAction,
    ApprovalApplication,
    ApprovalRecord,
    ApprovalStatus,
    BusinessType,
    MasterDataChange,
)


def test_approval_enum_values_are_stable() -> None:
    """验证审批状态、动作和业务类型严格使用任务约定的枚举值。"""
    assert [item.value for item in ApprovalStatus] == [
        "DRAFT",
        "IN_APPROVAL",
        "COMPLETED",
        "VOIDED",
    ]
    assert [item.value for item in ApprovalAction] == [
        "CREATE",
        "SAVE",
        "SUBMIT",
        "APPROVE",
        "REJECT",
        "WITHDRAW",
        "VOID",
    ]
    assert [item.value for item in BusinessType] == ["MASTER_DATA_CHANGE"]


def test_metadata_contains_only_the_three_approval_domain_tables() -> None:
    """验证共享 ORM metadata 仅注册本任务授权的三张业务表。"""
    assert set(Base.metadata.tables) == {
        "approval_application",
        "approval_record",
        "master_data_change",
    }


def test_application_defaults_and_draft_context_nullability() -> None:
    """验证申请状态与版本双重默认值，以及草稿流程上下文允许为空。"""
    table = ApprovalApplication.__table__
    assert table.c.status.default.arg is ApprovalStatus.DRAFT
    assert table.c.status.server_default.arg.text == "'DRAFT'"
    assert table.c.version.default.arg == 1
    assert table.c.version.server_default.arg.text == "1"
    for column_name in (
        "workflow_instance_id",
        "current_task_id",
        "current_node_code",
        "current_node_name",
    ):
        assert table.c[column_name].nullable


def test_external_identifiers_use_string_lengths_without_business_id_unique() -> None:
    """验证外部系统标识统一使用 String(128)，展示名称使用 String(255)。"""
    string_128_columns = {
        (ApprovalApplication, "business_id"),
        (ApprovalApplication, "applicant_id"),
        (ApprovalApplication, "workflow_instance_id"),
        (ApprovalApplication, "current_task_id"),
        (ApprovalApplication, "current_node_code"),
        (ApprovalRecord, "operator_id"),
        (ApprovalRecord, "from_task_id"),
        (ApprovalRecord, "from_node_code"),
        (ApprovalRecord, "target_task_id"),
        (ApprovalRecord, "target_node_code"),
        (ApprovalRecord, "target_operator_id"),
        (MasterDataChange, "entity_id"),
    }
    for model, column_name in string_128_columns:
        assert model.__table__.c[column_name].type.length == 128

    assert ApprovalApplication.__table__.c.current_node_name.type.length == 255
    assert ApprovalRecord.__table__.c.from_node_name.type.length == 255
    assert ApprovalRecord.__table__.c.target_node_name.type.length == 255
    assert not any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"business_id"}
        for constraint in ApprovalApplication.__table__.constraints
    )


def test_foreign_keys_are_restrictive_and_do_not_cascade_delete() -> None:
    """验证记录和变更只建立普通申请外键，不自动级联删除审计数据。"""
    for model in (ApprovalRecord, MasterDataChange):
        foreign_key = next(iter(model.__table__.c.approval_id.foreign_keys))
        assert isinstance(foreign_key, ForeignKey)
        assert foreign_key.target_fullname == "approval_application.id"
        assert foreign_key.ondelete is None


def test_master_data_jsonb_nullability_and_safe_python_defaults() -> None:
    """验证变更 JSONB 字段类型、空值策略与可变默认工厂。"""
    table = MasterDataChange.__table__
    assert isinstance(table.c.form_data.type, JSONB)
    assert isinstance(table.c.ext_data.type, JSONB)
    assert isinstance(table.c.change_items.type, JSONB)
    assert not table.c.form_data.nullable
    assert table.c.ext_data.nullable
    assert not table.c.change_items.nullable
    form_default_one = table.c.form_data.default.arg(None)
    form_default_two = table.c.form_data.default.arg(None)
    items_default_one = table.c.change_items.default.arg(None)
    items_default_two = table.c.change_items.default.arg(None)
    assert form_default_one == form_default_two == {}
    assert form_default_one is not form_default_two
    assert items_default_one == items_default_two == []
    assert items_default_one is not items_default_two


def test_models_have_explicit_non_native_enum_check_constraints() -> None:
    """验证数据库枚举列使用非 native Enum 和稳定显式约束名。"""
    expected = {
        (ApprovalApplication, "business_type"): "ck_approval_application_business_type",
        (ApprovalApplication, "status"): "ck_approval_application_status",
        (ApprovalRecord, "action"): "ck_approval_record_action",
        (ApprovalRecord, "from_status"): "ck_approval_record_from_status",
        (ApprovalRecord, "to_status"): "ck_approval_record_to_status",
    }
    for (model, column_name), constraint_name in expected.items():
        column_type = model.__table__.c[column_name].type
        assert isinstance(column_type, SqlEnum)
        assert not column_type.native_enum
        assert column_type.create_constraint
        assert column_type.name == constraint_name


def test_relationships_are_navigation_only_without_delete_cascade() -> None:
    """验证 ORM 导航关系正确且没有 delete 或 delete-orphan 级联。"""
    assert ApprovalApplication.records.property.mapper.class_ is ApprovalRecord
    assert not ApprovalApplication.master_data_change.property.uselist
    assert "delete" not in ApprovalApplication.records.property.cascade
    assert "delete-orphan" not in ApprovalApplication.records.property.cascade
    assert "delete" not in ApprovalApplication.master_data_change.property.cascade
    assert "delete-orphan" not in ApprovalApplication.master_data_change.property.cascade
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"approval_id"}
        for constraint in MasterDataChange.__table__.constraints
    )


def test_all_datetime_columns_are_timezone_aware_and_share_defaults() -> None:
    """验证模型的时间列统一使用 timezone-aware datetime 和数据库时钟默认值。"""
    tables = (
        ApprovalApplication.__table__,
        ApprovalRecord.__table__,
        MasterDataChange.__table__,
    )
    for table in tables:
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone
                if column.name in {"created_at", "updated_at"}:
                    assert column.server_default is not None
                    assert "now()" in str(column.server_default.arg).lower()
