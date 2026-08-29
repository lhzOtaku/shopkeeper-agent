"""
元数据知识构建配置

定义 meta_config.yaml 在程序中的结构，这份配置不描述底层基础设施
而是描述一次元数据知识构建要处理哪些表 哪些字段 和哪些指标，脚本入口会先读取 YAML
再把内容转换成这里定义的 dataclass 对象，后续服务层就可以按统一结构完成元数据同步
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ColumnConfig:
    """
    单个字段的同步配置
    描述字段的业务角色 说明 别名以及是否同步字段值
    """

    name: str
    role: str
    description: str
    alias: list[str]
    sync: bool


@dataclass
class TableConfig:
    """
    单张表及其字段列表的同步配置
    一张表下面会继续声明需要纳入知识库的字段列表
    """

    name: str
    role: str
    description: str
    columns: list[ColumnConfig]
    grain: str | None = None


@dataclass
class TableRelationConfig:
    """
    表关系同步配置
    用来描述 v2 数仓中稳定可用的 Join 路径
    """

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    relation_type: str
    description: str


@dataclass
class MetricVariantConfig:
    """
    指标在某个分析粒度下的具体计算口径。
    同一个业务指标可以有多个 variant，例如 GMV 在订单粒度和明细粒度下使用不同事实表。
    """

    name: str
    grain: str
    formula: str
    relevant_columns: list[str]
    default_filters: list[str] | None = None
    suitable_dimensions: list[str] | None = None
    base_table: str | None = None
    required_joins: list[str] | None = None


@dataclass
class MetricConfig:
    """
    单个指标的同步配置
    只描述指标身份和可选口径。公式、粒度和依赖字段由 variant 统一维护。
    """

    name: str
    description: str
    alias: list[str]
    default_variant: str
    variants: list[MetricVariantConfig]


@dataclass
class MetaConfig:
    """
    元数据知识构建总配置
    tables 和 metrics 都允许为空 便于分阶段只构建其中一部分
    """

    tables: Optional[list[TableConfig]] = None
    relations: Optional[list[TableRelationConfig]] = None
    metrics: Optional[list[MetricConfig]] = None
