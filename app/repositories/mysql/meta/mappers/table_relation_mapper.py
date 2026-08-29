"""
TableRelationInfo 映射器

负责在表关系业务实体和 ORM 模型之间做双向转换。
"""

from dataclasses import asdict

from app.entities.table_relation_info import TableRelationInfo
from app.models.table_relation import TableRelationMySQL


class TableRelationMapper:
    """负责 `TableRelationInfo` 与 `TableRelationMySQL` 之间的双向转换"""

    @staticmethod
    def to_entity(model: TableRelationMySQL) -> TableRelationInfo:
        """把表关系 ORM 模型转换为业务实体"""
        return TableRelationInfo(
            id=model.id,
            left_table=model.left_table,
            left_column=model.left_column,
            right_table=model.right_table,
            right_column=model.right_column,
            relation_type=model.relation_type,
            description=model.description,
        )

    @staticmethod
    def to_model(entity: TableRelationInfo) -> TableRelationMySQL:
        """把表关系业务实体转换为 ORM 模型"""
        return TableRelationMySQL(**asdict(entity))
