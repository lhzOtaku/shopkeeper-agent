"""
`metric_info` ORM 模型

定义元数据库中 metric_info 表对应的 ORM 模型。
它只保存指标身份；粒度、公式和字段依赖统一由 metric_variant 保存。
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class MetricInfoMySQL(Base):
    """指标元数据表对应的 ORM 模型"""

    __tablename__ = "metric_info"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="指标编码")
    name: Mapped[str | None] = mapped_column(String(128), comment="指标名称")
    description: Mapped[str | None] = mapped_column(Text, comment="指标描述")
    alias: Mapped[dict | list | None] = mapped_column(JSON, comment="指标别名")
    default_variant_id: Mapped[str] = mapped_column(
        String(128), comment="默认指标口径 ID"
    )
