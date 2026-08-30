from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.agents.ask_agent.nodes.enrich_generation_context import (
    enrich_generation_context,
)
from app.agents.ask_agent.nodes.extract_keywords import extract_keywords
from app.agents.ask_agent.nodes.filter_metric import filter_metric
from app.agents.ask_agent.nodes.filter_table import filter_table
from app.agents.ask_agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agents.ask_agent.nodes.recall_column import recall_column
from app.agents.ask_agent.nodes.recall_metric import recall_metric
from app.agents.ask_agent.nodes.recall_value import recall_value
from app.agents.ask_agent.nodes.run_sql import run_sql
from app.agents.ask_agent.nodes.select_metric_variant import select_metric_variant
from app.agents.ask_agent.nodes.validate_sql import validate_sql
from app.agents.ask_agent.state import MetricInfoState
from app.entities.metric_recall_candidate import MetricRecallCandidate

pytestmark = [pytest.mark.unit, pytest.mark.node]


async def test_extract_keywords_always_keeps_full_query(
    monkeypatch, event_runtime
):
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.extract_keywords.jieba.analyse.extract_tags",
        lambda *_args, **_kwargs: ["女装", "GMV"],
    )
    runtime, events = event_runtime()
    query = "统计2025年女装GMV"
    result = await extract_keywords({"query": query}, runtime)
    assert set(result["keywords"]) == {query, "女装", "GMV"}
    assert events[-1]["status"] == "success"


async def test_column_recall_uses_qdrant_and_deduplicates(
    monkeypatch, event_runtime, entity_factory
):
    column = entity_factory.column("dim_product.category_l2")
    repo = AsyncMock()
    repo.search.return_value = [column]
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_column.async_retry", AsyncMock(return_value=["品类"])
    )
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_column.embed_query_with_retry",
        AsyncMock(return_value=[0.1]),
    )
    runtime, _ = event_runtime(
        column_qdrant_repository=repo, embedding_client=AsyncMock()
    )
    result = await recall_column(
        {"query": "女装GMV", "keywords": ["女装"]}, runtime
    )
    assert result["retrieved_column_infos"] == [column]
    assert repo.search.await_count == 2


async def test_column_recall_partial_failure_returns_healthy_candidates(
    monkeypatch, event_runtime, entity_factory
):
    column = entity_factory.column("fact_order.order_amount", role="measure")
    repo = AsyncMock()
    repo.search.side_effect = [RuntimeError("qdrant down"), [column]]
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_column.async_retry", AsyncMock(return_value=["GMV"])
    )
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_column.embed_query_with_retry",
        AsyncMock(return_value=[0.1]),
    )
    runtime, _ = event_runtime(
        column_qdrant_repository=repo, embedding_client=AsyncMock()
    )
    result = await recall_column({"query": "GMV", "keywords": ["销售额"]}, runtime)
    assert result["retrieved_column_infos"] == [column]


async def test_metric_recall_hydrates_qdrant_ids_from_meta(
    monkeypatch, event_runtime, entity_factory
):
    metric = entity_factory.metric()
    qdrant = AsyncMock()
    qdrant.search.return_value = [
        MetricRecallCandidate("GMV", "GMV", "sales", ["销售额"])
    ]
    meta = AsyncMock()
    meta.get_metric_infos_by_ids.return_value = [metric]
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_metric.async_retry", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_metric.embed_query_with_retry",
        AsyncMock(return_value=[0.1]),
    )
    runtime, _ = event_runtime(
        embedding_client=AsyncMock(),
        metric_qdrant_repository=qdrant,
        meta_mysql_repository=meta,
    )
    result = await recall_metric({"query": "GMV", "keywords": ["GMV"]}, runtime)
    assert result["retrieved_metric_infos"] == [metric]
    meta.get_metric_infos_by_ids.assert_awaited_once_with(["GMV"])


async def test_metric_recall_uses_meta_fallback_when_qdrant_fails(
    monkeypatch, event_runtime, entity_factory
):
    metric = entity_factory.metric("订单数")
    qdrant = AsyncMock()
    qdrant.search.side_effect = RuntimeError("qdrant unavailable")
    meta = AsyncMock()
    meta.get_metric_infos_by_ids.return_value = []
    meta.search_metric_infos_by_texts.return_value = [metric]
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_metric.async_retry", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_metric.embed_query_with_retry",
        AsyncMock(return_value=[0.1]),
    )
    runtime, _ = event_runtime(
        embedding_client=AsyncMock(),
        metric_qdrant_repository=qdrant,
        meta_mysql_repository=meta,
    )
    result = await recall_metric({"query": "订单数", "keywords": ["订单数"]}, runtime)
    assert result["retrieved_metric_infos"] == [metric]
    meta.search_metric_infos_by_texts.assert_awaited_once()


async def test_value_recall_uses_elasticsearch_and_preserves_multi_field_hits(
    monkeypatch, event_runtime, entity_factory
):
    values = [
        entity_factory.value("女装", "dim_product.category_l2"),
        entity_factory.value("女装", "dim_product.product_name"),
    ]
    repo = AsyncMock()
    repo.search.return_value = values
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.recall_value.async_retry", AsyncMock(return_value=[])
    )
    runtime, _ = event_runtime(value_es_repository=repo)
    result = await recall_value({"query": "女装GMV", "keywords": ["女装"]}, runtime)
    assert {item.column_id for item in result["retrieved_value_infos"]} == {
        "dim_product.category_l2",
        "dim_product.product_name",
    }


async def test_merge_retrieval_adds_metric_columns_values_and_keys(
    event_runtime, entity_factory
):
    amount = entity_factory.column("fact_order.order_amount", role="measure")
    status = entity_factory.column("fact_order.order_status")
    order_id = entity_factory.column("fact_order.order_id", role="primary_key")
    metric = entity_factory.metric()
    meta = AsyncMock()
    meta.get_column_info_by_id.side_effect = lambda column_id: {
        status.id: status,
        "fact_order_item.item_amount": entity_factory.column(
            "fact_order_item.item_amount", role="measure"
        ),
        "fact_order_item.order_id": entity_factory.column(
            "fact_order_item.order_id", role="foreign_key"
        ),
    }.get(column_id)
    meta.get_key_columns_by_table_id.return_value = [order_id]
    meta.get_table_info_by_id.side_effect = lambda table_id: entity_factory.table(
        table_id, "order_item" if table_id == "fact_order_item" else "order"
    )
    runtime, _ = event_runtime(meta_mysql_repository=meta)
    result = await merge_retrieved_info(
        {
            "retrieved_column_infos": [amount],
            "retrieved_metric_infos": [metric],
            "retrieved_value_infos": [
                entity_factory.value("completed", "fact_order.order_status")
            ],
        },
        runtime,
    )
    names = {table["name"] for table in result["table_infos"]}
    assert {"fact_order", "fact_order_item"} <= names
    order = next(t for t in result["table_infos"] if t["name"] == "fact_order")
    assert {c["name"] for c in order["columns"]} >= {
        "order_amount",
        "order_status",
        "order_id",
    }


async def test_filter_table_keeps_only_llm_selected_columns_without_mutating_input(
    monkeypatch, event_runtime
):
    original = [
        {
            "name": "fact_order",
            "role": "fact",
            "description": "orders",
            "grain": "order",
            "columns": [
                {"name": "order_id"},
                {"name": "order_amount"},
                {"name": "pay_amount"},
            ],
        }
    ]
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.filter_table.async_retry",
        AsyncMock(return_value={"fact_order": ["order_id", "order_amount"]}),
    )
    runtime, _ = event_runtime()
    result = await filter_table({"query": "GMV", "table_infos": original}, runtime)
    assert [c["name"] for c in result["table_infos"][0]["columns"]] == [
        "order_id",
        "order_amount",
    ]
    assert len(original[0]["columns"]) == 3


async def test_filter_table_empty_candidates_are_explicitly_empty(
    monkeypatch, event_runtime
):
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.filter_table.async_retry", AsyncMock(return_value={})
    )
    runtime, _ = event_runtime()
    result = await filter_table(
        {"query": "不存在", "table_infos": [{"name": "fact_order", "columns": []}]},
        runtime,
    )
    assert result == {"table_infos": []}


async def test_filter_metric_resolves_product_refund_rate_deterministically(
    monkeypatch, event_runtime, entity_factory
):
    order_rate = entity_factory.metric("订单退款率")
    quantity_rate = entity_factory.metric("件数退款率")
    monkeypatch.setattr(
        "app.agents.ask_agent.nodes.filter_metric.async_retry",
        AsyncMock(return_value=["订单退款率"]),
    )
    meta = AsyncMock()
    meta.get_metric_infos_by_ids.return_value = [quantity_rate]
    runtime, _ = event_runtime(meta_mysql_repository=meta)
    result = await filter_metric(
        {
            "query": "统计女装退款率",
            "metric_infos": [
                MetricInfoState(
                    name=order_rate.name,
                    description=order_rate.description,
                    alias=order_rate.alias,
                    default_variant_id=order_rate.default_variant_id,
                    variants=[asdict(v) for v in order_rate.variants],
                )
            ],
            "retrieved_value_infos": [
                entity_factory.value("女装", "dim_product.category_l2")
            ],
        },
        runtime,
    )
    assert [item["name"] for item in result["metric_infos"]] == ["件数退款率"]


async def test_select_metric_variant_is_deterministic_and_does_not_call_llm(
    event_runtime, entity_factory
):
    metric = entity_factory.metric()
    state_metric = MetricInfoState(
        name=metric.name,
        description=metric.description,
        alias=metric.alias,
        default_variant_id=metric.default_variant_id,
        variants=[asdict(v) for v in metric.variants],
    )
    runtime, _ = event_runtime()
    result = await select_metric_variant(
        {
            "query": "统计女装商品销售额",
            "table_infos": [{"name": "dim_product"}],
            "metric_infos": [state_metric],
            "retrieved_value_infos": [
                entity_factory.value("女装", "dim_product.category_l2")
            ],
        },
        runtime,
    )
    selected = result["metric_infos"][0]["selected_variant"]
    assert selected["name"] == "item_variant"
    assert "score=" in selected["selection_reason"]


async def test_enrich_context_adds_meta_columns_relations_and_strong_value_binding(
    event_runtime, entity_factory
):
    meta = AsyncMock()
    columns = {
        "fact_order_item.item_amount": entity_factory.column(
            "fact_order_item.item_amount", role="measure"
        ),
        "fact_order_item.order_id": entity_factory.column(
            "fact_order_item.order_id", role="foreign_key"
        ),
        "fact_order.order_status": entity_factory.column("fact_order.order_status"),
        "fact_order.order_id": entity_factory.column(
            "fact_order.order_id", role="primary_key"
        ),
        "dim_product.category_l2": entity_factory.column(
            "dim_product.category_l2", examples=[]
        ),
        "dim_product.product_id": entity_factory.column(
            "dim_product.product_id", role="primary_key"
        ),
    }
    meta.get_column_info_by_id.side_effect = lambda cid: columns.get(cid)
    meta.get_table_info_by_id.side_effect = lambda tid: entity_factory.table(
        tid, {"fact_order_item": "order_item", "dim_product": "product"}.get(tid, "order")
    )
    meta.get_relations_by_table_ids.return_value = [
        entity_factory.relation(
            "fact_order_item", "product_id", "dim_product", "product_id"
        )
    ]
    metric = entity_factory.metric()
    selected = asdict(metric.variants[1])
    dw = AsyncMock()
    dw.get_db_info.return_value = {"dialect": "mysql", "version": "8.0"}
    runtime, _ = event_runtime(
        meta_mysql_repository=meta,
        dw_mysql_repository=dw,
    )
    result = await enrich_generation_context(
        {
            "query": "统计女装商品销售额",
            "metric_infos": [
                {
                    "name": "GMV",
                    "description": "sales",
                    "alias": ["销售额"],
                    "selected_variant": selected,
                }
            ],
            "table_infos": [
                {
                    "name": "dim_product",
                    "role": "dim",
                    "description": "products",
                    "grain": "product",
                    "columns": [],
                }
            ],
            "retrieved_value_infos": [
                entity_factory.value("女装", "dim_product.category_l2"),
                entity_factory.value("食品", "dim_product.category_l1"),
            ],
        },
        runtime,
    )
    context = result["generation_context"]
    assert [v["value"] for v in context["value_bindings"]] == ["女装"]
    assert any(
        j["join_condition"]
        == "fact_order_item.product_id = dim_product.product_id"
        for j in context["join_relations"]
    )
    assert {t["name"] for t in context["tables"]} >= {
        "fact_order_item",
        "dim_product",
    }
    assert context["db_info"] == {"dialect": "mysql", "version": "8.0"}
    assert context["date_info"]["quarter"].startswith("Q")
    dw.get_db_info.assert_awaited_once()


async def test_validate_sql_calls_explain_repository_on_safe_select(
    event_runtime
):
    dw = AsyncMock()
    runtime, _ = event_runtime(dw_mysql_repository=dw)
    state = {
        "sql": "SELECT SUM(order_amount) FROM fact_order",
        "generation_context": {
            "tables": [
                {
                    "name": "fact_order",
                    "columns": [{"name": "order_amount"}],
                }
            ]
        },
    }
    result = await validate_sql(state, runtime)
    assert result["error"] is None
    assert result["sql_safety_blocked"] is False
    dw.validate.assert_awaited_once_with(state["sql"])


async def test_validate_sql_blocks_mutation_without_repository_call(event_runtime):
    dw = AsyncMock()
    runtime, events = event_runtime(dw_mysql_repository=dw)
    result = await validate_sql({"sql": "DELETE FROM fact_order"}, runtime)
    assert result["sql_safety_blocked"] is True
    dw.validate.assert_not_awaited()
    assert any(event.get("code") == "SQL_SAFETY_BLOCKED" for event in events)


async def test_validate_sql_explain_failure_is_correctable_state(event_runtime):
    dw = AsyncMock()
    dw.validate.side_effect = RuntimeError("Unknown column")
    runtime, _ = event_runtime(dw_mysql_repository=dw)
    result = await validate_sql(
        {
            "sql": "SELECT missing FROM fact_order",
            "generation_context": {
                "tables": [
                    {"name": "fact_order", "columns": [{"name": "missing"}]}
                ]
            },
        },
        runtime,
    )
    assert result["error"] == "Unknown column"
    assert result["sql_safety_blocked"] is False


async def test_run_sql_returns_empty_rows_as_success_and_builds_query_spec(
    event_runtime
):
    dw = AsyncMock()
    dw.run.return_value = []
    runtime, events = event_runtime(dw_mysql_repository=dw)
    result = await run_sql(
        {"query": "统计无数据区间", "sql": "SELECT 1 WHERE FALSE"}, runtime
    )
    assert result["rows"] == []
    assert result["query_spec"]["result_summary"]["row_count"] == 0
    assert any(event.get("type") == "result" for event in events)


async def test_run_sql_propagates_repository_failure(event_runtime):
    dw = AsyncMock()
    dw.run.side_effect = RuntimeError("DW execution failed")
    runtime, events = event_runtime(dw_mysql_repository=dw)
    with pytest.raises(RuntimeError, match="DW execution failed"):
        await run_sql({"query": "q", "sql": "SELECT 1"}, runtime)
    assert events[-1]["status"] == "error"
