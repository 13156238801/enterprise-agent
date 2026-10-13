"""创建版本化 Workflow Definition 快照表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d1f004a203bc"
down_revision: str | Sequence[str] | None = "b15a8d09c2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建流程版本快照表及版本和 active 唯一性约束。"""
    op.create_table(
        "workflow_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "definition_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.CheckConstraint("version > 0", name="ck_workflow_definitions_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_workflow_definitions_code_version"),
    )
    op.create_index(
        "uq_workflow_definitions_active_code",
        "workflow_definitions",
        ["code"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    """仅删除本迁移新增的 Workflow Definition 表。"""
    op.drop_index("uq_workflow_definitions_active_code", table_name="workflow_definitions")
    op.drop_table("workflow_definitions")
