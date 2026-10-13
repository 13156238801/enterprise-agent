"""新增当前主数据快照并演进变更草稿字段。"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c5a005d1f005"
down_revision: str | Sequence[str] | None = "d1f004a203bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建主数据快照表并为历史草稿安全补齐 before 与基线版本。"""
    op.create_table(
        "master_data_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("data_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.CheckConstraint("version > 0", name="ck_master_data_entities_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "entity_key", name="uq_master_data_entities_type_key"),
    )

    op.alter_column(
        "master_data_change",
        "entity_id",
        new_column_name="entity_key",
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
    op.add_column(
        "master_data_change",
        sa.Column("before_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "master_data_change",
        sa.Column("base_entity_version", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text("UPDATE master_data_change SET before_data = form_data WHERE before_data IS NULL")
    )
    op.execute(
        sa.text(
            "UPDATE master_data_change "
            "SET base_entity_version = 1 WHERE base_entity_version IS NULL"
        )
    )
    op.alter_column(
        "master_data_change",
        "before_data",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.alter_column(
        "master_data_change",
        "base_entity_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_master_data_change_base_version_positive",
        "master_data_change",
        "base_entity_version > 0",
    )


def downgrade() -> None:
    """移除当前主数据表并将草稿 schema 恢复到 P1-004 结构。"""
    op.drop_table("master_data_entities")
    op.drop_constraint(
        "ck_master_data_change_base_version_positive",
        "master_data_change",
        type_="check",
    )
    op.drop_column("master_data_change", "base_entity_version")
    op.drop_column("master_data_change", "before_data")
    op.alter_column(
        "master_data_change",
        "entity_key",
        new_column_name="entity_id",
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
