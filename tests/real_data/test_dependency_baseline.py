from types import SimpleNamespace

import pytest

from app.agents.ask_agent.nodes.enrich_generation_context import enrich_generation_context
from app.conf.app_config import app_config

pytestmark = [pytest.mark.integration, pytest.mark.real_data]


async def test_meta_mysql_contains_authoritative_tables_fields_and_relations(
    real_repositories
):
    expected = {
        "dim_date",
        "dim_region",
        "dim_product",
        "dim_member",
        "dim_channel",
        "fact_order",
        "fact_order_item",
        "fact_refund",
    }
    found = set()
    for table_id in expected:
        table = await real_repositories.meta.get_table_info_by_id(table_id)
        assert table is not None
        assert table.grain
        found.add(table.id)
    assert found == expected
    assert (
        await real_repositories.meta.get_column_info_by_id(
            "fact_order.order_amount"
        )
    ).role == "measure"
    relations = await real_repositories.meta.get_relations_by_table_ids(
        sorted(expected)
    )
    assert len(relations) == 9


async def test_meta_metric_definitions_variants_and_required_columns(real_repositories):
    metrics = await real_repositories.meta.get_metric_infos_by_ids(
        ["GMV", "订单数", "销量", "客单价", "退款金额", "订单退款率", "件数退款率"]
    )
    by_name = {metric.name: metric for metric in metrics}
    assert set(by_name) == {
        "GMV",
        "订单数",
        "销量",
        "客单价",
        "退款金额",
        "订单退款率",
        "件数退款率",
    }
    assert len(by_name["GMV"].variants) == 2
    assert {variant.base_table for variant in by_name["GMV"].variants} == {
        "fact_order",
        "fact_order_item",
    }
    assert any(
        "COUNT(DISTINCT fact_order.order_id)" in variant.formula
        for variant in by_name["订单数"].variants
    )
    assert any(
        "fact_refund.refund_status" in variant.relevant_columns
        for variant in by_name["退款金额"].variants
    )


async def test_dw_schema_grains_keys_and_date_range_are_real(real_repositories):
    tables = await real_repositories.dw.run(
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema = DATABASE() ORDER BY table_name"
    )
    assert {row["name"] for row in tables} == {
        "dim_channel",
        "dim_date",
        "dim_member",
        "dim_product",
        "dim_region",
        "fact_order",
        "fact_order_item",
        "fact_refund",
    }
    dates = await real_repositories.dw.run(
        "SELECT MIN(date_value) AS min_date, MAX(date_value) AS max_date FROM dim_date"
    )
    assert str(dates[0]["min_date"]) == "2024-01-01"
    assert str(dates[0]["max_date"]) == "2026-06-30"
    order_columns = await real_repositories.dw.get_column_types("fact_order")
    assert order_columns["order_id"].startswith("varchar")
    assert order_columns["order_amount"].startswith("decimal")


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("dim_channel", "channel_name", "直播"),
        ("dim_region", "region_name", "华东"),
        ("dim_product", "category_l2", "女装"),
        ("fact_order", "order_status", "cancelled"),
        ("fact_refund", "refund_status", "success"),
    ],
)
async def test_verified_business_enumerations_exist(
    table, column, expected, real_repositories
):
    values = await real_repositories.dw.get_column_values(table, column, limit=100)
    assert expected in values


async def test_dw_positive_metric_examples_are_non_empty_and_semantically_distinct(
    real_repositories,
):
    order = await real_repositories.dw.run(
        "SELECT COUNT(DISTINCT fo.order_id) AS order_count, "
        "SUM(fo.order_amount) AS order_gmv, SUM(fo.pay_amount) AS pay_amount "
        "FROM fact_order fo JOIN dim_date d ON d.date_id=fo.date_id "
        "WHERE d.year=2025 AND fo.order_status<>'cancelled'"
    )
    item = await real_repositories.dw.run(
        "SELECT SUM(oi.item_amount) AS item_gmv, SUM(oi.quantity) AS sales_quantity "
        "FROM fact_order_item oi JOIN fact_order fo ON fo.order_id=oi.order_id "
        "JOIN dim_product p ON p.product_id=oi.product_id "
        "JOIN dim_date d ON d.date_id=fo.date_id "
        "WHERE d.year=2025 AND p.category_l2='女装' "
        "AND fo.order_status<>'cancelled'"
    )
    refund = await real_repositories.dw.run(
        "SELECT SUM(r.refund_amount) AS refund_amount, "
        "SUM(r.refund_quantity) AS refund_quantity "
        "FROM fact_refund r JOIN dim_date d ON d.date_id=r.date_id "
        "WHERE d.year=2025 AND r.refund_status='success'"
    )
    assert order[0]["order_count"] > 0
    assert order[0]["order_gmv"] > order[0]["pay_amount"] > 0
    assert item[0]["item_gmv"] > 0 and item[0]["sales_quantity"] > 0
    assert refund[0]["refund_amount"] > 0 and refund[0]["refund_quantity"] > 0


async def test_order_to_item_join_demonstrates_real_amplification_risk(
    real_repositories,
):
    result = await real_repositories.dw.run(
        "SELECT COUNT(*) AS joined_rows, COUNT(DISTINCT fo.order_id) AS orders "
        "FROM fact_order fo JOIN fact_order_item oi ON oi.order_id=fo.order_id "
        "WHERE fo.order_status<>'cancelled'"
    )
    assert result[0]["joined_rows"] > result[0]["orders"]


async def test_qdrant_collections_have_expected_vector_and_payload_shapes(
    real_repositories,
):
    for name, required in (
        (app_config.qdrant.column_collection_name, {"id", "name", "table_id"}),
        (app_config.qdrant.metric_collection_name, {"metric_id", "name"}),
    ):
        info = await real_repositories.qdrant.get_collection(name)
        assert info.points_count > 0
        assert info.config.params.vectors.size == app_config.qdrant.embedding_size
        points, _ = await real_repositories.qdrant.scroll(
            collection_name=name, limit=1, with_payload=True, with_vectors=False
        )
        assert required <= set(points[0].payload)


async def test_qdrant_natural_language_top_k_recalls_real_field_and_metric(
    real_repositories,
):
    embedding = await real_repositories.embedding.aembed_query("2025年女装销售额")
    columns = await real_repositories.columns.search(
        embedding, score_threshold=0.0, limit=10
    )
    metrics = await real_repositories.metrics.search(
        embedding, score_threshold=0.0, limit=10
    )
    assert "fact_order_item.item_amount" in {column.id for column in columns}
    assert "GMV" in {metric.metric_id for metric in metrics}


async def test_elasticsearch_mapping_and_women_clothing_multi_field_recall(
    real_repositories,
):
    index = app_config.es.index_name
    mapping = await real_repositories.es.indices.get_mapping(index=index)
    properties = mapping[index]["mappings"]["properties"]
    assert {"id", "value", "column_id", "table_id", "column_name"} <= set(
        properties
    )
    values = await real_repositories.values.search(
        "女装", score_threshold=0.0, limit=30
    )
    columns = {value.column_id for value in values}
    assert "dim_product.category_l2" in columns
    assert "dim_product.product_name" in columns


@pytest.mark.parametrize("value", ["女装", "手机", "耳机"])
async def test_real_value_ambiguity_can_hit_category_and_product_name(
    value, real_repositories
):
    values = await real_repositories.values.search(value, score_threshold=0.0, limit=30)
    columns = {item.column_id for item in values}
    assert "dim_product.category_l2" in columns
    assert "dim_product.product_name" in columns


async def test_four_sources_can_form_one_generation_context(real_repositories):
    metric = (await real_repositories.meta.get_metric_infos_by_ids(["GMV"]))[0]
    item_variant = next(v for v in metric.variants if v.base_table == "fact_order_item")
    values = await real_repositories.values.search("女装", score_threshold=0.0, limit=20)
    category = next(v for v in values if v.column_id == "dim_product.category_l2")
    product_table = await real_repositories.meta.get_table_info_by_id("dim_product")
    product_column = await real_repositories.meta.get_column_info_by_id(
        "dim_product.category_l2"
    )
    state = {
        "query": "统计2025年女装品类GMV",
        "metric_infos": [
            {
                "name": metric.name,
                "description": metric.description,
                "alias": metric.alias,
                "selected_variant": {
                    **item_variant.__dict__,
                },
            }
        ],
        "table_infos": [
            {
                "name": product_table.name,
                "role": product_table.role,
                "description": product_table.description,
                "grain": product_table.grain,
                "columns": [
                    {
                        "name": product_column.name,
                        "type": product_column.type,
                        "role": product_column.role,
                        "examples": product_column.examples,
                        "description": product_column.description,
                        "alias": product_column.alias,
                    }
                ],
            }
        ],
        "retrieved_value_infos": [category],
    }
    runtime = SimpleNamespace(
        context={"meta_mysql_repository": real_repositories.meta},
        stream_writer=lambda _event: None,
    )
    result = await enrich_generation_context(state, runtime)
    context = result["generation_context"]
    assert context["metrics"][0]["selected_variant"]["base_table"] == "fact_order_item"
    assert context["value_bindings"][0]["value"] == "女装"
    assert {table["name"] for table in context["tables"]} >= {
        "fact_order_item",
        "dim_product",
    }


async def test_real_sql_explain_and_execution_are_separate_checks(real_repositories):
    sql = (
        "SELECT p.category_l2, SUM(oi.item_amount) AS gmv "
        "FROM fact_order_item oi "
        "JOIN fact_order fo ON fo.order_id=oi.order_id "
        "JOIN dim_product p ON p.product_id=oi.product_id "
        "JOIN dim_date d ON d.date_id=fo.date_id "
        "WHERE d.year=2025 AND p.category_l2='女装' "
        "AND fo.order_status<>'cancelled' GROUP BY p.category_l2"
    )
    await real_repositories.dw.validate(sql)
    rows = await real_repositories.dw.run(sql)
    assert rows and rows[0]["category_l2"] == "女装"
    assert rows[0]["gmv"] > 0
