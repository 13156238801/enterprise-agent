"""定义版本化工作流模板的 PostgreSQL 持久化模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class WorkflowDefinition(Base):
    """保存不可变流程版本快照及可变的 active 选择状态。"""

    __tablename__ = "workflow_definitions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_workflow_definitions_version_positive"),
        UniqueConstraint("code", "version", name="uq_workflow_definitions_code_version"),
        # 部分唯一索引让数据库并发写入时也至多保留一个 active 版本。
        Index(
            "uq_workflow_definitions_active_code",
            "code",
            unique=True,
            postgresql_where=text("active = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
