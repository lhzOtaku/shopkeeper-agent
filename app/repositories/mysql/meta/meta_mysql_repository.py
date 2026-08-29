"""
元数据库 MySQL 仓储

这一层对应文档里的 Meta Repository，负责接收业务实体并落到 Meta MySQL
Repository 自身只关心“如何写入”，而“哪些写操作要放在同一笔事务里”，由 Service 层统一决定

表 字段 指标和字段指标关系都会先以业务实体流转，再在这里统一转成 ORM 模型
问数链路运行时也会从这里读取元数据，用来把召回到的 id 补齐成完整实体
"""

import re

from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.metric_variant_column import MetricVariantColumn
from app.entities.table_info import TableInfo
from app.entities.table_relation_info import TableRelationInfo
from app.models.column_info import ColumnInfoMySQL
from app.models.metric_info import MetricInfoMySQL
from app.models.metric_variant import MetricVariantMySQL
from app.models.metric_variant_column import MetricVariantColumnMySQL
from app.models.table_info import TableInfoMySQL
from app.models.table_relation import TableRelationMySQL
from app.repositories.mysql.meta.mappers.column_info_mapper import ColumnInfoMapper
from app.repositories.mysql.meta.mappers.metric_info_mapper import MetricInfoMapper
from app.repositories.mysql.meta.mappers.metric_variant_mapper import (
    MetricVariantColumnMapper,
    MetricVariantMapper,
)
from app.repositories.mysql.meta.mappers.table_info_mapper import TableInfoMapper
from app.repositories.mysql.meta.mappers.table_relation_mapper import (
    TableRelationMapper,
)


class MetaMySQLRepository:
    """负责把元数据业务实体持久化到 Meta MySQL"""

    def __init__(self, session: AsyncSession):
        self.session = session

    def save_table_infos(self, table_infos: list[TableInfo]):
        """批量保存表元数据。输入仍然是业务实体，而不是 ORM 模型"""
        self.session.add_all(
            [TableInfoMapper.to_model(table_info) for table_info in table_infos]
        )

    def save_column_infos(self, column_infos: list[ColumnInfo]):
        """批量保存字段元数据。实体到模型的转换统一通过 Mapper 完成"""
        self.session.add_all(
            [ColumnInfoMapper.to_model(column_info) for column_info in column_infos]
        )

    def save_metric_infos(self, metric_infos: list[MetricInfo]):
        """批量保存指标元数据。指标本身和字段关联关系分开写入"""
        self.session.add_all(
            [MetricInfoMapper.to_model(metric_info) for metric_info in metric_infos]
        )

    def save_metric_variants(self, metric_variants):
        """批量保存每个指标在具体粒度下的可执行口径。"""
        self.session.add_all(
            [
                MetricVariantMapper.to_model(metric_variant)
                for metric_variant in metric_variants
            ]
        )

    def save_metric_variant_columns(
        self, metric_variant_columns: list[MetricVariantColumn]
    ):
        """批量保存口径与字段之间的依赖关系。"""
        self.session.add_all(
            [
                MetricVariantColumnMapper.to_model(metric_variant_column)
                for metric_variant_column in metric_variant_columns
            ]
        )

    def save_table_relations(self, table_relations: list[TableRelationInfo]):
        """批量保存表关系元数据。"""
        self.session.add_all(
            [
                TableRelationMapper.to_model(table_relation)
                for table_relation in table_relations
            ]
        )

    async def delete_table_and_column_infos(
        self, table_ids: list[str], column_ids: list[str]
    ):
        """删除指定范围内的表字段元数据，便于重复构建覆盖旧版本。"""

        column_conditions = []
        if table_ids:
            column_conditions.append(ColumnInfoMySQL.table_id.in_(table_ids))
        if column_ids:
            column_conditions.append(ColumnInfoMySQL.id.in_(column_ids))

        if column_conditions:
            target_column_ids = select(ColumnInfoMySQL.id).where(
                or_(*column_conditions)
            )
            await self.session.execute(
                delete(MetricVariantColumnMySQL).where(
                    MetricVariantColumnMySQL.column_id.in_(target_column_ids)
                )
            )
            await self.session.execute(
                delete(ColumnInfoMySQL).where(or_(*column_conditions))
            )

        if table_ids:
            await self.session.execute(
                delete(TableRelationMySQL).where(
                    or_(
                        TableRelationMySQL.left_table.in_(table_ids),
                        TableRelationMySQL.right_table.in_(table_ids),
                    )
                )
            )
            await self.session.execute(
                delete(TableInfoMySQL).where(TableInfoMySQL.id.in_(table_ids))
            )

    async def delete_table_relations(self, relation_ids: list[str]):
        """按关系 id 删除表关系元数据，便于重复构建覆盖旧版本。"""

        if not relation_ids:
            return

        await self.session.execute(
            delete(TableRelationMySQL).where(TableRelationMySQL.id.in_(relation_ids))
        )

    async def delete_metric_infos(self, metric_ids: list[str]):
        """删除指定指标、口径及口径字段依赖，便于重复构建覆盖旧版本。"""

        if not metric_ids:
            return

        target_variant_ids = select(MetricVariantMySQL.id).where(
            MetricVariantMySQL.metric_id.in_(metric_ids)
        )
        await self.session.execute(
            delete(MetricVariantColumnMySQL).where(
                MetricVariantColumnMySQL.variant_id.in_(target_variant_ids)
            )
        )
        await self.session.execute(
            delete(MetricVariantMySQL).where(MetricVariantMySQL.metric_id.in_(metric_ids))
        )
        await self.session.execute(
            delete(MetricInfoMySQL).where(MetricInfoMySQL.id.in_(metric_ids))
        )

    async def get_metric_infos_by_ids(self, metric_ids: list[str]) -> list[MetricInfo]:
        """批量补齐候选指标的全部 variants 与字段依赖。"""

        ordered_metric_ids = list(dict.fromkeys(metric_ids))
        if not ordered_metric_ids:
            return []

        metric_result = await self.session.execute(
            select(MetricInfoMySQL).where(MetricInfoMySQL.id.in_(ordered_metric_ids))
        )
        metric_infos = {
            metric_info.id: MetricInfoMapper.to_entity(metric_info)
            for metric_info in metric_result.scalars().fetchall()
        }
        if not metric_infos:
            return []

        variant_result = await self.session.execute(
            select(MetricVariantMySQL).where(
                MetricVariantMySQL.metric_id.in_(metric_infos)
            )
        )
        variant_models = variant_result.scalars().fetchall()
        variant_ids = [variant_model.id for variant_model in variant_models]
        variant_columns_map: dict[str, list[str]] = {
            variant_id: [] for variant_id in variant_ids
        }

        if variant_ids:
            variant_column_result = await self.session.execute(
                select(MetricVariantColumnMySQL).where(
                    MetricVariantColumnMySQL.variant_id.in_(variant_ids)
                )
            )
            for variant_column in variant_column_result.scalars().fetchall():
                dependency = MetricVariantColumnMapper.to_entity(variant_column)
                variant_columns_map[dependency.variant_id].append(dependency.column_id)

        for variant_model in variant_models:
            metric_infos[variant_model.metric_id].variants.append(
                MetricVariantMapper.to_entity(
                    variant_model,
                    variant_columns_map.get(variant_model.id, []),
                )
            )

        return [
            metric_infos[metric_id]
            for metric_id in ordered_metric_ids
            if metric_id in metric_infos
        ]

    async def get_column_info_by_id(self, id: str) -> ColumnInfo | None:
        """按字段 id 查询字段元数据，供召回信息合并阶段补齐字段上下文"""

        column_info: ColumnInfoMySQL | None = await self.session.get(
            ColumnInfoMySQL, id
        )
        if column_info:
            return ColumnInfoMapper.to_entity(column_info)
        else:
            return None

    async def get_table_info_by_id(self, id: str) -> TableInfo | None:
        """按表 id 查询表元数据，最终组装成提示词里的表结构信息"""

        table_info: TableInfoMySQL | None = await self.session.get(TableInfoMySQL, id)
        if table_info:
            return TableInfoMapper.to_entity(table_info)
        else:
            return None

    async def search_metric_infos_by_texts(
        self, texts: list[str], limit: int = 20
    ) -> list[MetricInfo]:
        """Fallback metric retrieval from Meta MySQL when vector retrieval is unstable."""

        terms = self._normalize_search_terms(texts)
        if not terms:
            return []

        result = await self.session.execute(select(MetricInfoMySQL))
        metric_infos = [
            MetricInfoMapper.to_entity(model) for model in result.scalars().fetchall()
        ]

        scored_metric_infos: list[tuple[int, MetricInfo]] = []
        for metric_info in metric_infos:
            score = self._score_metric_info(metric_info, terms)
            if score > 0:
                scored_metric_infos.append((score, metric_info))

        scored_metric_infos.sort(key=lambda item: item[0], reverse=True)
        selected_ids = [
            metric_info.id for _, metric_info in scored_metric_infos[:limit]
        ]
        return await self.get_metric_infos_by_ids(selected_ids)

    @staticmethod
    def _normalize_search_terms(texts: list[str]) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for raw_text in texts:
            if not raw_text:
                continue
            candidates = [
                str(raw_text),
                *re.split(r"[\s,，。；;、/|]+", str(raw_text)),
            ]
            for candidate in candidates:
                term = candidate.strip().lower()
                if term and term not in seen:
                    seen.add(term)
                    terms.append(term)
        return terms

    @classmethod
    def _score_metric_info(cls, metric_info: MetricInfo, terms: list[str]) -> int:
        names = cls._flatten_texts([metric_info.id, metric_info.name])
        aliases = cls._flatten_texts(metric_info.alias)
        searchable_text = " ".join(
            cls._flatten_texts(
                [
                    metric_info.id,
                    metric_info.name,
                    metric_info.description,
                    metric_info.alias,
                    metric_info.default_variant_id,
                ]
            )
        ).lower()

        score = 0
        for term in terms:
            if any(term == name or (name and name in term) for name in names):
                score += 100
            elif any(term == alias or (alias and alias in term) for alias in aliases):
                score += 80
            elif any(term in name or name in term for name in names if name):
                score += 50
            elif any(term in alias or alias in term for alias in aliases if alias):
                score += 40
            elif term in searchable_text:
                score += 10
        return score

    @classmethod
    def _flatten_texts(cls, value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.lower()]
        if isinstance(value, dict):
            texts: list[str] = []
            for key, item in value.items():
                texts.extend(cls._flatten_texts(key))
                texts.extend(cls._flatten_texts(item))
            return texts
        if isinstance(value, (list, tuple, set)):
            texts: list[str] = []
            for item in value:
                texts.extend(cls._flatten_texts(item))
            return texts
        return [str(value).lower()]

    async def get_key_columns_by_table_id(self, table_id: str) -> list[ColumnInfo]:
        """查询指定表的主外键字段，避免 Join 关键字段被向量召回漏掉"""

        # 主外键字段用于后续生成 join 条件，不能完全依赖向量召回命中
        sql = "select * from column_info where table_id = :table_id and role in ('primary_key','foreign_key')"
        # :table_id 是 SQLAlchemy text SQL 的占位符，实际值通过第二个参数传入
        result = await self.session.execute(text(sql), {"table_id": table_id})
        # mappings() 会把结果行转成类似字典的结构，便于解包成 ColumnInfo
        return [ColumnInfo(**dict(row)) for row in result.mappings().fetchall()]

    async def get_relations_by_table_ids(
        self, table_ids: list[str]
    ) -> list[TableRelationInfo]:
        """查询候选表之间可用的 Join 关系。"""

        if not table_ids:
            return []

        result = await self.session.execute(
            select(TableRelationMySQL).where(
                or_(
                    TableRelationMySQL.left_table.in_(table_ids),
                    TableRelationMySQL.right_table.in_(table_ids),
                )
            )
        )
        return [
            TableRelationMapper.to_entity(model)
            for model in result.scalars().fetchall()
        ]
