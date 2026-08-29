"""ORM model for metric variant to column dependencies."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetricVariantColumnMySQL(Base):
    """One field dependency belonging to one executable metric variant."""

    __tablename__ = "metric_variant_column"

    variant_id: Mapped[str] = mapped_column(
        String(128), primary_key=True, comment="指标口径 ID"
    )
    column_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="字段 ID"
    )
