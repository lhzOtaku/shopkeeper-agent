"""
元数据知识构建服务

负责组织元数据知识库构建的核心业务流程，位于脚本入口和仓储层之间
一方面接收配置文件，另一方面协调元数据库和数仓查询仓储

当前这条主线已经覆盖表字段入库 字段向量索引 字段取值全文索引
以及指标入库和指标向量索引构建逻辑
"""

import uuid
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from langchain_huggingface import HuggingFaceEndpointEmbeddings
from omegaconf import OmegaConf

from app.conf.meta_config import MetaConfig
from app.core.log import logger
from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo, MetricVariantInfo
from app.entities.metric_recall_candidate import MetricRecallCandidate
from app.entities.metric_variant_column import MetricVariantColumn
from app.entities.table_info import TableInfo
from app.entities.table_relation_info import TableRelationInfo
from app.entities.value_info import ValueInfo
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


class MetaKnowledgeService:
    """负责串联元数据知识库构建流程的应用服务"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository | None = None,
        embedding_client: HuggingFaceEndpointEmbeddings | None = None,
        value_es_repository: ValueESRepository | None = None,
        metric_qdrant_repository: MetricQdrantRepository | None = None,
    ):
        # meta repository 负责结构化元数据的落库
        self.meta_mysql_repository: MetaMySQLRepository = meta_mysql_repository
        # dw repository 负责到教学数仓中读取真实表结构和示例值
        self.dw_mysql_repository: DWMySQLRepository = dw_mysql_repository
        # 字段向量集合的创建和写入统一交给 Qdrant Repository
        self.column_qdrant_repository: ColumnQdrantRepository | None = (
            column_qdrant_repository
        )
        # 向量化动作放在 Service 层
        self.embedding_client: HuggingFaceEndpointEmbeddings | None = embedding_client
        # 字段值全文索引的写入统一交给 ES Repository
        self.value_es_repository: ValueESRepository | None = value_es_repository
        # 指标向量集合和字段向量集合分开管理，便于后续按对象类型独立召回
        self.metric_qdrant_repository: MetricQdrantRepository | None = (
            metric_qdrant_repository
        )

    def _to_json_value(self, value: Any) -> Any:
        """把数据库原生类型转换成 JSON 和检索索引可接受的值。"""

        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    def _to_json_values(self, values: list[Any]) -> list[Any]:
        """批量转换字段示例值。"""

        return [self._to_json_value(value) for value in values]

    async def _save_tables_to_meta_db(
        self, meta_config: MetaConfig
    ) -> list[ColumnInfo]:
        """把配置里的表字段信息补齐后写入 Meta MySQL"""
        table_infos: list[TableInfo] = []
        column_infos: list[ColumnInfo] = []

        for table in meta_config.tables:
            # 先把配置里的表定义整理成业务实体，后面统一交给 Meta Repository 落库
            table_info = TableInfo(
                id=table.name,
                name=table.name,
                role=table.role,
                description=table.description,
                grain=table.grain,
            )
            table_infos.append(table_info)

            # 字段类型属于数仓里的真实信息，所以这里仍然要回到 DW 查询
            column_types = await self.dw_mysql_repository.get_column_types(table.name)

            for column in table.columns:
                # 这里只拿少量示例值，目的是让字段元数据更容易被人和模型理解
                column_values = await self.dw_mysql_repository.get_column_values(
                    table.name, column.name, 10
                )
                column_values = self._to_json_values(column_values)
                # 字段 id 使用 table.column 形式，后续在向量索引和全文索引里都会复用
                column_info = ColumnInfo(
                    id=f"{table.name}.{column.name}",
                    name=column.name,
                    type=column_types[column.name],
                    role=column.role,
                    examples=column_values,
                    description=column.description,
                    alias=column.alias,
                    table_id=table.name,
                )
                column_infos.append(column_info)

        table_ids = [table_info.id for table_info in table_infos]
        column_ids = [column_info.id for column_info in column_infos]
        async with self.meta_mysql_repository.session.begin():
            await self.meta_mysql_repository.delete_table_and_column_infos(
                table_ids, column_ids
            )
            self.meta_mysql_repository.save_table_infos(table_infos)
            self.meta_mysql_repository.save_column_infos(column_infos)

        return column_infos

    async def _save_column_info_to_qdrant(self, column_infos: list[ColumnInfo]):
        """把字段元数据继续推进成可语义检索的 Qdrant 向量点"""
        if self.column_qdrant_repository is None or self.embedding_client is None:
            raise RuntimeError("Qdrant and Embedding clients must be configured")

        await self.column_qdrant_repository.ensure_collection()

        points: list[dict] = []
        for column_info in column_infos:
            # 一个字段不会只生成一个向量点，而是把名字 描述 别名都拆开建立语义入口
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": column_info.name,
                    "payload": asdict(column_info),
                }
            )

            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": column_info.description,
                    "payload": asdict(column_info),
                }
            )

            for alia in column_info.alias:
                points.append(
                    {
                        "id": uuid.uuid4(),
                        "embedding_text": alia,
                        "payload": asdict(column_info),
                    }
                )

        # 先把待向量化文本抽出来，再分批调用 Embedding 服务
        # 这样更容易控制单次请求大小
        embeddings: list[list[float]] = []
        embedding_texts = [point["embedding_text"] for point in points]
        embedding_batch_size = 20
        for i in range(0, len(embedding_texts), embedding_batch_size):
            batch_embedding_texts = embedding_texts[i : i + embedding_batch_size]
            batch_embeddings = await self.embedding_client.aembed_documents(
                batch_embedding_texts
            )
            embeddings.extend(batch_embeddings)

        ids = [point["id"] for point in points]
        payloads = [point["payload"] for point in points]

        await self.column_qdrant_repository.upsert(ids, embeddings, payloads)

    async def _save_value_info_to_es(
        self, meta_config: MetaConfig, column_infos: list[ColumnInfo]
    ):
        """把允许同步的字段真实取值写入 Elasticsearch 全文索引"""
        if self.value_es_repository is None:
            raise RuntimeError("Elasticsearch client must be configured")

        await self.value_es_repository.ensure_index()

        # 不是所有字段都要同步真实值，是否同步由配置里的 sync 显式控制
        column2sync: dict[str, bool] = {}
        for table in meta_config.tables:
            for column in table.columns:
                column2sync[f"{table.name}.{column.name}"] = column.sync

        value_infos: list[ValueInfo] = []
        for column_info in column_infos:
            sync = column2sync[column_info.id]
            if sync:
                # 这里拿的是字段真实值全集，不再是第 8 章里的少量 examples
                current_column_values = (
                    await self.dw_mysql_repository.get_column_values(
                        column_info.table_id, column_info.name, 100000
                    )
                )
                current_values_infos: list[ValueInfo] = []
                for current_column_value in current_column_values:
                    json_value = self._to_json_value(current_column_value)
                    if json_value is None:
                        continue
                    text_value = str(json_value)
                    current_values_infos.append(
                        ValueInfo(
                            id=f"{column_info.id}.{text_value}",
                            value=text_value,
                            column_id=column_info.id,
                            table_id=column_info.table_id,
                            column_name=column_info.name,
                            column_alias=column_info.alias,
                            field_role=column_info.role,
                        )
                    )
                value_infos.extend(current_values_infos)

        await self.value_es_repository.index(value_infos)

    async def _save_relations_to_meta_db(self, meta_config: MetaConfig):
        """把配置里的表关系写入 Meta MySQL"""
        table_relations: list[TableRelationInfo] = []

        for relation in meta_config.relations or []:
            relation_id = (
                f"{relation.left_table}.{relation.left_column}"
                f"->{relation.right_table}.{relation.right_column}"
            )
            table_relations.append(
                TableRelationInfo(
                    id=relation_id,
                    left_table=relation.left_table,
                    left_column=relation.left_column,
                    right_table=relation.right_table,
                    right_column=relation.right_column,
                    relation_type=relation.relation_type,
                    description=relation.description,
                )
            )

        if not table_relations:
            return

        relation_ids = [table_relation.id for table_relation in table_relations]
        async with self.meta_mysql_repository.session.begin():
            await self.meta_mysql_repository.delete_table_relations(relation_ids)
            self.meta_mysql_repository.save_table_relations(table_relations)

    async def _save_metrics_to_meta_db(
        self, meta_config: MetaConfig
    ) -> list[MetricInfo]:
        """把指标身份、可执行口径和字段依赖分别写入 Meta MySQL。"""
        metric_infos: list[MetricInfo] = []
        metric_variants: list[MetricVariantInfo] = []
        metric_variant_columns: list[MetricVariantColumn] = []

        for metric in meta_config.metrics:
            variant_names = [variant.name for variant in metric.variants]
            if not variant_names:
                raise ValueError(f"Metric '{metric.name}' must define at least one variant")
            if len(variant_names) != len(set(variant_names)):
                raise ValueError(f"Metric '{metric.name}' has duplicate variant names")
            if metric.default_variant not in variant_names:
                raise ValueError(
                    f"Metric '{metric.name}' default variant "
                    f"'{metric.default_variant}' is not defined"
                )

            variants: list[MetricVariantInfo] = []
            for variant in metric.variants:
                variant_id = f"{metric.name}.{variant.name}"
                metric_variant = MetricVariantInfo(
                    id=variant_id,
                    metric_id=metric.name,
                    name=variant.name,
                    grain=variant.grain,
                    formula=variant.formula,
                    relevant_columns=variant.relevant_columns,
                    default_filters=variant.default_filters or [],
                    suitable_dimensions=variant.suitable_dimensions or [],
                    base_table=variant.base_table,
                    required_joins=variant.required_joins or [],
                )
                variants.append(metric_variant)
                metric_variants.append(metric_variant)
                metric_variant_columns.extend(
                    MetricVariantColumn(variant_id=variant_id, column_id=column_id)
                    for column_id in variant.relevant_columns
                )

            default_variant_id = f"{metric.name}.{metric.default_variant}"
            metric_info = MetricInfo(
                id=metric.name,
                name=metric.name,
                description=metric.description,
                alias=metric.alias,
                default_variant_id=default_variant_id,
                variants=variants,
            )
            metric_infos.append(metric_info)

        metric_ids = [metric_info.id for metric_info in metric_infos]

        # 指标身份、口径和口径字段关系必须在同一笔事务中写入。
        async with self.meta_mysql_repository.session.begin():
            await self.meta_mysql_repository.delete_metric_infos(metric_ids)
            self.meta_mysql_repository.save_metric_infos(metric_infos)
            self.meta_mysql_repository.save_metric_variants(metric_variants)
            self.meta_mysql_repository.save_metric_variant_columns(metric_variant_columns)

        return metric_infos

    async def _save_metrics_to_qdrant(self, metric_infos: list[MetricInfo]):
        """把指标身份推进成轻量 Qdrant 语义召回点。"""
        if self.metric_qdrant_repository is None or self.embedding_client is None:
            raise RuntimeError("Qdrant and Embedding clients must be configured")

        await self.metric_qdrant_repository.ensure_collection()

        points: list[dict] = []
        for metric_info in metric_infos:
            recall_candidate = MetricRecallCandidate(
                metric_id=metric_info.id,
                name=metric_info.name,
                description=metric_info.description,
                alias=metric_info.alias,
            )
            # 和字段一样，一个指标也会拆成名字 描述 别名这几类语义入口
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": metric_info.name,
                    "payload": asdict(recall_candidate),
                }
            )

            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": metric_info.description,
                    "payload": asdict(recall_candidate),
                }
            )

            for alia in metric_info.alias:
                points.append(
                    {
                        "id": uuid.uuid4(),
                        "embedding_text": alia,
                        "payload": asdict(recall_candidate),
                    }
                )

        # 先把待向量化文本抽出来，再分批调用 Embedding 服务
        # 返回的 embeddings 要继续和前面的 id payload 按顺序对齐
        embeddings: list[list[float]] = []
        embedding_texts = [point["embedding_text"] for point in points]
        embedding_batch_size = 20
        for i in range(0, len(embedding_texts), embedding_batch_size):
            batch_embedding_texts = embedding_texts[i : i + embedding_batch_size]
            batch_embeddings = await self.embedding_client.aembed_documents(
                batch_embedding_texts
            )
            embeddings.extend(batch_embeddings)

        ids = [point["id"] for point in points]
        payloads = [point["payload"] for point in points]

        await self.metric_qdrant_repository.upsert(ids, embeddings, payloads)

    async def build(self, config_path: Path):
        """读取配置并依次构建 Meta MySQL Qdrant 和 ES 中的元数据索引"""
        context = OmegaConf.load(config_path)
        schema = OmegaConf.structured(MetaConfig)
        meta_config: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))

        # 根据配置文件判断后续要进入哪条构建链路
        if meta_config.tables:
            # 将表信息和字段信息保存到 Meta MySQL
            column_infos = await self._save_tables_to_meta_db(meta_config)
            logger.info(
                f"保存表信息和字段信息到 Meta MySQL 成功："
                f"{len(meta_config.tables)} 张表，{len(column_infos)} 个字段"
            )

            # 对字段信息建立向量索引
            await self._save_column_info_to_qdrant(column_infos)
            logger.info("为字段信息建立向量索引成功")

            # 对指定的维度字段取值建立全文索引
            await self._save_value_info_to_es(meta_config, column_infos)
            logger.info("为字段取值建立全文索引成功")

        if meta_config.relations:
            await self._save_relations_to_meta_db(meta_config)
            logger.info("保存表关系信息到数据库成功")

        # 根据配置文件同步指定的指标信息
        if meta_config.metrics:
            # 将指标信息和字段依赖关系保存到 Meta MySQL
            metric_infos = await self._save_metrics_to_meta_db(meta_config)
            logger.info("保存指标信息到数据库成功")

            # 对指标信息建立向量索引
            await self._save_metrics_to_qdrant(metric_infos)
            logger.info("为指标信息建立向量索引成功")
