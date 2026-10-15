from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from app.models.approval import ApprovalApplication


class MasterDataChange(Base):
    """保存审批申请对应的主数据变更表单、扩展数据和字段差异。"""

    __tablename__ = "master_data_change"
    __table_args__ = (
        UniqueConstraint("approval_id", name="uq_master_data_change_approval_id"),
        CheckConstraint(
            "base_entity_version > 0",
            name="ck_master_data_change_base_version_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_application.id", name="fk_master_data_change_approval_id"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    before_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    base_entity_version: Mapped[int] = mapped_column(Integer, nullable=False)
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


class MasterDataEntity(Base):
    """保存当前生效的主数据 JSON 快照和正向递增版本。"""

    __tablename__ = "master_data_entities"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_key", name="uq_master_data_entities_type_key"),
        CheckConstraint("version > 0", name="ck_master_data_entities_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
