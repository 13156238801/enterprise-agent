from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from app.models.approval import ApprovalApplication


class MasterDataChange(Base):
    """保存审批申请对应的主数据变更表单、扩展数据和字段差异。"""

    __tablename__ = "master_data_change"
    __table_args__ = (UniqueConstraint("approval_id", name="uq_master_data_change_approval_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_application.id", name="fk_master_data_change_approval_id"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    form_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ext_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    change_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    approval: Mapped[ApprovalApplication] = relationship(back_populates="master_data_change")
