"""
表关系业务实体

用于表达 v2 数仓中稳定的 Join 路径，让知识库构建阶段把表关系
写入 Meta MySQL，后续问数链路可以从元数据中读取可用关联关系。
"""

from dataclasses import dataclass


@dataclass
class TableRelationInfo:
    """系统内部统一使用的表关系表达"""

    id: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    relation_type: str
    description: str
