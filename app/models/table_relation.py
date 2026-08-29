"""
`table_relation` ORM 模型

定义元数据库中表关系表对应的 ORM 模型，用于保存 v2 数仓中可用的
Join 路径，避免 SQL 生成阶段完全依赖 LLM 猜测表关联。
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TableRelationMySQL(Base):
    """表关系元数据表对应的 ORM 模型"""

    __tablename__ = "table_relation"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="关系编号")
    left_table: Mapped[str | None] = mapped_column(String(64), comment="左表")
    left_column: Mapped[str | None] = mapped_column(String(64), comment="左字段")
    right_table: Mapped[str | None] = mapped_column(String(64), comment="右表")
    right_column: Mapped[str | None] = mapped_column(String(64), comment="右字段")
    relation_type: Mapped[str | None] = mapped_column(
        String(32), comment="关系类型"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="关系描述")
