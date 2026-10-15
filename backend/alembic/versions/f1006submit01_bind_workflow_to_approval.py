"""为审批申请增加不可变工作流版本绑定字段。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1006submit01"
down_revision: str | Sequence[str] | None = "c5a005d1f005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加 nullable 流程绑定、成对及正版本约束和复合外键。"""
    op.add_column(
        "approval_application", sa.Column("workflow_code", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "approval_application", sa.Column("workflow_version", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_approval_application_workflow_binding_pair",
        "approval_application",
        "(workflow_code IS NULL AND workflow_version IS NULL) "
        "OR (workflow_code IS NOT NULL AND workflow_version IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_approval_application_workflow_version_positive",
        "approval_application",
        "workflow_version IS NULL OR workflow_version > 0",
    )
    op.create_foreign_key(
        "fk_approval_application_workflow_definition",
        "approval_application",
        "workflow_definitions",
        ["workflow_code", "workflow_version"],
        ["code", "version"],
    )


def downgrade() -> None:
    """只移除 P1-006 新增的约束与两列，保留此前全部领域表。"""
    op.drop_constraint(
        "fk_approval_application_workflow_definition", "approval_application", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_approval_application_workflow_version_positive",
        "approval_application",
        type_="check",
    )
    op.drop_constraint(
        "ck_approval_application_workflow_binding_pair",
        "approval_application",
        type_="check",
    )
    op.drop_column("approval_application", "workflow_version")
    op.drop_column("approval_application", "workflow_code")
