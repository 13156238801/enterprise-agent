"""创建 Approval Domain 首批三张业务表。

Revision ID: 7aeecb191822
Revises:
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7aeecb191822"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建申请、动作历史和主数据变更表及其约束、索引。"""
    op.create_table(
        "approval_application",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "business_type",
            sa.Enum(
                "MASTER_DATA_CHANGE",
                name="ck_approval_application_business_type",
                native_enum=False,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("business_id", sa.String(length=128), nullable=False),
        sa.Column("applicant_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "IN_APPROVAL",
                "COMPLETED",
                "VOIDED",
                name="ck_approval_application_status",
                native_enum=False,
                create_constraint=False,
            ),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column("workflow_instance_id", sa.String(length=128), nullable=True),
        sa.Column("current_task_id", sa.String(length=128), nullable=True),
        sa.Column("current_node_code", sa.String(length=128), nullable=True),
        sa.Column("current_node_name", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "business_type IN ('MASTER_DATA_CHANGE')",
            name="ck_approval_application_business_type",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'IN_APPROVAL', 'COMPLETED', 'VOIDED')",
            name="ck_approval_application_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_application_status", "approval_application", ["status"], unique=False
    )
    op.create_index(
        "ix_approval_application_applicant_id",
        "approval_application",
        ["applicant_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_application_business_type",
        "approval_application",
        ["business_type"],
        unique=False,
    )
    op.create_table(
        "approval_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.String(length=128), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "CREATE",
                "SAVE",
                "SUBMIT",
                "APPROVE",
                "REJECT",
                "WITHDRAW",
                "VOID",
                name="ck_approval_record_action",
                native_enum=False,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "from_status",
            sa.Enum(
                "DRAFT",
                "IN_APPROVAL",
                "COMPLETED",
                "VOIDED",
                name="ck_approval_record_from_status",
                native_enum=False,
                create_constraint=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum(
                "DRAFT",
                "IN_APPROVAL",
                "COMPLETED",
                "VOIDED",
                name="ck_approval_record_to_status",
                native_enum=False,
                create_constraint=False,
            ),
            nullable=False,
        ),
        sa.Column("from_task_id", sa.String(length=128), nullable=True),
        sa.Column("from_node_code", sa.String(length=128), nullable=True),
        sa.Column("from_node_name", sa.String(length=255), nullable=True),
        sa.Column("target_task_id", sa.String(length=128), nullable=True),
        sa.Column("target_node_code", sa.String(length=128), nullable=True),
        sa.Column("target_node_name", sa.String(length=255), nullable=True),
        sa.Column("target_operator_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('CREATE', 'SAVE', 'SUBMIT', 'APPROVE', 'REJECT', 'WITHDRAW', 'VOID')",
            name="ck_approval_record_action",
        ),
        sa.CheckConstraint(
            "from_status IN ('DRAFT', 'IN_APPROVAL', 'COMPLETED', 'VOIDED')",
            name="ck_approval_record_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('DRAFT', 'IN_APPROVAL', 'COMPLETED', 'VOIDED')",
            name="ck_approval_record_to_status",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval_application.id"],
            name="fk_approval_record_approval_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_record_approval_id", "approval_record", ["approval_id"], unique=False
    )
    op.create_table(
        "master_data_change",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("form_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ext_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("change_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval_application.id"],
            name="fk_master_data_change_approval_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id", name="uq_master_data_change_approval_id"),
    )


def downgrade() -> None:
    """按依赖顺序删除本迁移创建的三张表。"""
    op.drop_table("master_data_change")
    op.drop_index("ix_approval_record_approval_id", table_name="approval_record")
    op.drop_table("approval_record")
    op.drop_index("ix_approval_application_business_type", table_name="approval_application")
    op.drop_index("ix_approval_application_applicant_id", table_name="approval_application")
    op.drop_index("ix_approval_application_status", table_name="approval_application")
    op.drop_table("approval_application")
