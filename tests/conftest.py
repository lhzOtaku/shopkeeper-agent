from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo, MetricVariantInfo
from app.entities.table_info import TableInfo
from app.entities.table_relation_info import TableRelationInfo
from app.entities.value_info import ValueInfo
from app.memory.session_memory import SessionMemory


@pytest.fixture
def event_runtime():
    def factory(**context):
        events: list[dict] = []
        return SimpleNamespace(context=context, stream_writer=events.append), events

    return factory


@pytest.fixture
def base_query_spec():
    return {
        "original_query": "统计2025年女装GMV",
        "resolved_query": "统计2025年女装GMV",
        "metrics": [
            {
                "name": "GMV",
                "description": "有效订单成交金额",
                "formula": "SUM(fact_order_item.item_amount)",
            }
        ],
        "tables": [
            {
                "name": "fact_order_item",
                "description": "订单商品明细事实表",
            }
        ],
        "filters": [
            {
                "field": "dim_product.category_l2",
                "operator": "=",
                "value": "女装",
            }
        ],
        "dimensions": [],
        "time_range": {
            "field": "dim_date.date_value",
            "start": "2025-01-01",
            "end": "2026-01-01",
        },
        "sql": "SELECT SUM(oi.item_amount) AS gmv FROM fact_order_item oi",
        "result_summary": {"row_count": 1, "columns": ["gmv"]},
    }


@pytest.fixture
def successful_memory(base_query_spec):
    memory = SessionMemory(session_id="session-a")
    memory.remember_successful_query(
        raw_user_message="统计2025年女装GMV",
        resolved_query="统计2025年女装GMV",
        query_spec=deepcopy(base_query_spec),
    )
    return memory


@pytest.fixture
def clarifying_pending():
    return {
        "phase": "clarifying",
        "origin_intent": "followup_query",
        "original_user_message": "那这个呢",
        "clarification_count": 1,
        "clarification_history": [
            {"round": 1, "question": "要追加还是替换指标？", "answer": None}
        ],
        "candidate_resolved_query": None,
        "resolved_query": None,
        "execution_retry_count": 0,
        "retryable": False,
        "last_error": None,
    }


@pytest.fixture
def ready_pending():
    return {
        "phase": "ready_to_execute",
        "origin_intent": "followup_query",
        "original_user_message": "那直播渠道呢",
        "clarification_count": 0,
        "clarification_history": [],
        "candidate_resolved_query": None,
        "resolved_query": "统计2025年直播渠道女装GMV",
        "execution_retry_count": 0,
        "retryable": True,
        "last_error": "connection failed",
    }


@pytest.fixture
def entity_factory():
    def column(
        column_id: str,
        *,
        role: str = "dimension",
        examples: list | None = None,
        alias: list[str] | None = None,
    ) -> ColumnInfo:
        table_id, name = column_id.split(".", 1)
        return ColumnInfo(
            id=column_id,
            name=name,
            type="decimal(12,2)" if role == "measure" else "varchar(50)",
            role=role,
            examples=list(examples or []),
            description=f"{name} description",
            alias=list(alias or [name]),
            table_id=table_id,
        )

    def value(value: str, column_id: str) -> ValueInfo:
        table_id, name = column_id.split(".", 1)
        return ValueInfo(
            id=f"{column_id}.{value}",
            value=value,
            column_id=column_id,
            table_id=table_id,
            column_name=name,
            column_alias=[name],
            field_role="dimension",
        )

    def table(table_id: str, grain: str = "order") -> TableInfo:
        return TableInfo(
            id=table_id,
            name=table_id,
            role="fact" if table_id.startswith("fact_") else "dim",
            description=f"{table_id} description",
            grain=grain,
        )

    def relation(left: str, left_col: str, right: str, right_col: str):
        return TableRelationInfo(
            id=f"{left}.{left_col}->{right}.{right_col}",
            left_table=left,
            left_column=left_col,
            right_table=right,
            right_column=right_col,
            relation_type="many_to_one",
            description="join relation",
        )

    def metric(name: str = "GMV") -> MetricInfo:
        order = MetricVariantInfo(
            id=f"{name}.order",
            metric_id=name,
            name="order_variant",
            grain="order",
            formula="SUM(fact_order.order_amount)",
            relevant_columns=[
                "fact_order.order_amount",
                "fact_order.order_status",
            ],
            default_filters=["fact_order.order_status <> 'cancelled'"],
            suitable_dimensions=["dim_date", "dim_region", "dim_channel"],
            base_table="fact_order",
            required_joins=[],
        )
        item = MetricVariantInfo(
            id=f"{name}.item",
            metric_id=name,
            name="item_variant",
            grain="order_item",
            formula="SUM(fact_order_item.item_amount)",
            relevant_columns=[
                "fact_order_item.item_amount",
                "fact_order_item.order_id",
            ],
            default_filters=["fact_order.order_status <> 'cancelled'"],
            suitable_dimensions=["dim_product", "dim_date"],
            base_table="fact_order_item",
            required_joins=[
                "fact_order_item.order_id = fact_order.order_id"
            ],
        )
        return MetricInfo(
            id=name,
            name=name,
            description=f"{name} description",
            alias=["销售额"],
            default_variant_id=order.id,
            variants=[order, item],
        )

    return SimpleNamespace(
        column=column,
        value=value,
        table=table,
        relation=relation,
        metric=metric,
    )
