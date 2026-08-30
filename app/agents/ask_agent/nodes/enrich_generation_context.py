"""
生成前上下文增强节点。

这个节点把新知识库中的结构化信息整合回原 askAgent 数据流：
- ES 字段值召回 -> value_bindings
- Meta 表粒度 -> enriched tables
- Meta 表关系 -> join_relations
- Metric selected_variant -> selected metric specs and required columns
- 当前日期和 DW 数据库信息 -> date_info and db_info
"""

import re
from collections import defaultdict
from datetime import date

from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.state import (
    ColumnInfoState,
    DataAgentState,
    DateInfoState,
    DBInfoState,
    GenerationContextState,
    MetricInfoState,
    TableInfoState,
    TableRelationState,
    ValueBindingState,
)
from app.core.log import logger
from app.entities.column_info import ColumnInfo
from app.entities.table_info import TableInfo
from app.entities.table_relation_info import TableRelationInfo

FIELD_REF_PATTERN = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")


def _column_state(column_info: ColumnInfo) -> ColumnInfoState:
    return ColumnInfoState(
        name=column_info.name,
        type=column_info.type,
        role=column_info.role,
        examples=column_info.examples,
        description=column_info.description,
        alias=column_info.alias,
    )


def _table_state(table_info: TableInfo, columns: list[ColumnInfoState]) -> TableInfoState:
    return TableInfoState(
        name=table_info.name,
        role=table_info.role,
        description=table_info.description,
        grain=table_info.grain,
        columns=columns,
    )


def _field_refs(text: str | None) -> set[str]:
    if not text:
        return set()
    return {f"{table}.{column}" for table, column in FIELD_REF_PATTERN.findall(text)}


def _column_id_parts(column_id: str) -> tuple[str, str] | None:
    if "." not in column_id:
        return None
    table_id, column_name = column_id.split(".", 1)
    if not table_id or not column_name:
        return None
    return table_id, column_name


def _is_strong_value_binding(query: str, value: str | None) -> bool:
    if not value:
        return False
    return str(value).lower() in query.lower()


async def _ensure_table(
    table_id: str,
    table_meta_map: dict[str, TableInfo],
    table_columns_map: dict[str, dict[str, ColumnInfoState]],
    meta_mysql_repository,
) -> TableInfo | None:
    if table_id in table_meta_map:
        return table_meta_map[table_id]

    table_info = await meta_mysql_repository.get_table_info_by_id(table_id)
    if table_info is None:
        return None

    table_meta_map[table_id] = table_info
    table_columns_map.setdefault(table_id, {})
    return table_info


async def _ensure_column(
    column_id: str,
    table_meta_map: dict[str, TableInfo],
    table_columns_map: dict[str, dict[str, ColumnInfoState]],
    meta_mysql_repository,
) -> ColumnInfoState | None:
    parts = _column_id_parts(column_id)
    if parts is None:
        return None

    table_id, column_name = parts
    table_info = await _ensure_table(
        table_id, table_meta_map, table_columns_map, meta_mysql_repository
    )
    if table_info is None:
        return None

    table_columns = table_columns_map.setdefault(table_id, {})
    if column_name in table_columns:
        return table_columns[column_name]

    column_info = await meta_mysql_repository.get_column_info_by_id(column_id)
    if column_info is None:
        return None

    column_state = _column_state(column_info)
    table_columns[column_name] = column_state
    return column_state


def _build_metric_specs(
    metric_infos: list[MetricInfoState],
) -> tuple[list[MetricInfoState], set[str]]:
    metric_specs: list[MetricInfoState] = []
    required_column_ids: set[str] = set()

    for metric_info in metric_infos:
        selected_variant = metric_info.get("selected_variant")
        if selected_variant:
            required_column_ids.update(selected_variant.get("relevant_columns") or [])
            for default_filter in selected_variant.get("default_filters") or []:
                required_column_ids.update(_field_refs(default_filter))

        metric_specs.append(
            MetricInfoState(
                name=metric_info.get("name"),
                description=metric_info.get("description"),
                alias=metric_info.get("alias") or [],
                selected_variant=selected_variant,
            )
        )

    return metric_specs, required_column_ids


def _relation_state(relation: TableRelationInfo) -> TableRelationState:
    return TableRelationState(
        left_table=relation.left_table,
        left_column=relation.left_column,
        right_table=relation.right_table,
        right_column=relation.right_column,
        relation_type=relation.relation_type,
        description=relation.description,
        join_condition=(
            f"{relation.left_table}.{relation.left_column} = "
            f"{relation.right_table}.{relation.right_column}"
        ),
    )


async def enrich_generation_context(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):
    """把新知识库结果整理成 SQL 生成前的最终结构化上下文。"""

    writer = runtime.stream_writer
    step = "补充生成上下文"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        metric_infos = state.get("metric_infos", [])
        table_infos = state.get("table_infos", [])
        retrieved_value_infos = state.get("retrieved_value_infos", [])
        meta_mysql_repository = runtime.context["meta_mysql_repository"]
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        table_meta_map: dict[str, TableInfo] = {}
        table_columns_map: dict[str, dict[str, ColumnInfoState]] = defaultdict(dict)

        for table_info_state in table_infos:
            table_id = table_info_state["name"]
            table_meta_map[table_id] = TableInfo(
                id=table_id,
                name=table_id,
                role=table_info_state["role"],
                description=table_info_state["description"],
                grain=table_info_state.get("grain"),
            )
            for column in table_info_state.get("columns", []):
                table_columns_map[table_id][column["name"]] = column

        metric_specs, required_column_ids = _build_metric_specs(metric_infos)
        for column_id in sorted(required_column_ids):
            await _ensure_column(
                column_id,
                table_meta_map,
                table_columns_map,
                meta_mysql_repository,
            )

        value_bindings_map: dict[str, ValueBindingState] = {}
        for value_info in retrieved_value_infos:
            if not _is_strong_value_binding(query, value_info.value):
                continue

            column_id = value_info.column_id
            parts = _column_id_parts(column_id)
            if parts is None:
                continue

            table_id, column_name = parts
            column_state = await _ensure_column(
                column_id,
                table_meta_map,
                table_columns_map,
                meta_mysql_repository,
            )
            if column_state is None:
                continue

            if value_info.value not in column_state["examples"]:
                column_state["examples"].append(value_info.value)

            value_bindings_map[value_info.id] = ValueBindingState(
                value=value_info.value,
                column_id=column_id,
                table_id=value_info.table_id or table_id,
                column_name=value_info.column_name or column_name,
                column_alias=value_info.column_alias or [],
                field_role=value_info.field_role,
            )

        relation_states: list[TableRelationState] = []
        table_ids = sorted(table_meta_map)
        relations = await meta_mysql_repository.get_relations_by_table_ids(table_ids)
        for relation in relations:
            if (
                relation.left_table not in table_meta_map
                or relation.right_table not in table_meta_map
            ):
                continue

            await _ensure_column(
                f"{relation.left_table}.{relation.left_column}",
                table_meta_map,
                table_columns_map,
                meta_mysql_repository,
            )
            await _ensure_column(
                f"{relation.right_table}.{relation.right_column}",
                table_meta_map,
                table_columns_map,
                meta_mysql_repository,
            )
            relation_states.append(_relation_state(relation))

        enriched_table_infos = [
            _table_state(
                table_meta_map[table_id],
                list(table_columns_map[table_id].values()),
            )
            for table_id in sorted(table_meta_map)
        ]

        today = date.today()
        date_info = DateInfoState(
            date=today.strftime("%Y-%m-%d"),
            weekday=today.strftime("%A"),
            quarter=f"Q{(today.month - 1) // 3 + 1}",
        )
        db_info = DBInfoState(**(await dw_mysql_repository.get_db_info()))

        generation_context = GenerationContextState(
            metrics=metric_specs,
            tables=enriched_table_infos,
            value_bindings=list(value_bindings_map.values()),
            join_relations=relation_states,
            date_info=date_info,
            db_info=db_info,
        )

        logger.info(
            "生成上下文补齐完成："
            f"tables={table_ids}, "
            f"values={[(v['column_id'], v['value']) for v in generation_context['value_bindings']]}, "
            f"joins={[j['join_condition'] for j in generation_context['join_relations']]}, "
            f"date={date_info['date']}, "
            f"db={db_info['dialect']} {db_info['version']}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "table_infos": enriched_table_infos,
            "generation_context": generation_context,
        }
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
