"""ORM model for executable metric variants."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class MetricVariantMySQL(Base):
    """One formula under one analysis grain for a business metric."""

    __tablename__ = "metric_variant"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="口径 ID")
    metric_id: Mapped[str] = mapped_column(String(64), comment="所属指标 ID")
    name: Mapped[str] = mapped_column(String(128), comment="口径名称")
    grain: Mapped[str] = mapped_column(String(64), comment="计算粒度")
    base_table: Mapped[str | None] = mapped_column(String(128), comment="基础事实表")
    formula: Mapped[str] = mapped_column(Text, comment="指标公式")
    default_filters: Mapped[dict | list | None] = mapped_column(
        JSON, comment="默认过滤条件"
    )
    suitable_dimensions: Mapped[dict | list | None] = mapped_column(
        JSON, comment="适用维度表"
    )
    required_joins: Mapped[dict | list | None] = mapped_column(
        JSON, comment="额外 Join 约束"
    )
