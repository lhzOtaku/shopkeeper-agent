import pytest

from app.agents.ask_agent.graph import MAX_SQL_CORRECTION_ATTEMPTS, route_after_sql_validation
from app.agents.ask_agent.sql_guard import validate_sql_safety
from app.memory.query_spec import build_query_spec

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("state", "route"),
    [
        ({"error": None}, "run_sql"),
        ({"error": "unknown column", "correction_count": 0}, "correct_sql"),
        (
            {
                "error": "unknown column",
                "correction_count": MAX_SQL_CORRECTION_ATTEMPTS,
            },
            "fail_sql_correction",
        ),
        (
            {"error": "unsafe", "sql_safety_blocked": True},
            "fail_sql_correction",
        ),
    ],
)
def test_sql_validation_route_is_a_deterministic_conditional_edge(state, route):
    assert route_after_sql_validation(state) == route


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO fact_order(order_id) VALUES ('x')",
        "UPDATE fact_order SET order_amount = 0",
        "DELETE FROM fact_order",
        "DROP TABLE fact_order",
        "CREATE TABLE x(id INT)",
        "ALTER TABLE fact_order ADD COLUMN x INT",
        "TRUNCATE TABLE fact_order",
    ],
)
def test_sql_guard_blocks_every_mutating_statement(sql):
    result = validate_sql_safety(sql, {})
    assert result.ok is False
    assert result.blocked is True


def test_sql_guard_allows_read_only_aggregate_over_known_context():
    context = {
        "tables": [
            {
                "name": "fact_order",
                "columns": [
                    {"name": "order_amount"},
                    {"name": "order_status"},
                ],
            }
        ]
    }
    result = validate_sql_safety(
        "SELECT SUM(order_amount) FROM fact_order WHERE order_status <> 'cancelled'",
        context,
    )
    assert result.ok is True


def test_query_spec_extracts_metric_filter_grain_time_and_result_shape():
    sql = (
        "SELECT p.category_l2, SUM(oi.item_amount) AS gmv "
        "FROM fact_order_item oi JOIN dim_product p ON p.product_id=oi.product_id "
        "WHERE d.date_value >= '2025-01-01' AND d.date_value < '2026-01-01' "
        "GROUP BY p.category_l2"
    )
    context = {
        "metrics": [
            {
                "name": "GMV",
                "description": "sales",
                "selected_variant": {
                    "name": "item_gmv",
                    "grain": "order_item",
                    "formula": "SUM(fact_order_item.item_amount)",
                    "base_table": "fact_order_item",
                    "required_joins": [],
                },
            }
        ],
        "value_bindings": [
            {
                "value": "女装",
                "column_id": "dim_product.category_l2",
                "table_id": "dim_product",
                "column_name": "category_l2",
                "field_role": "dimension",
            }
        ],
        "tables": [
            {
                "name": "fact_order_item",
                "role": "fact",
                "description": "items",
                "grain": "order_item",
            }
        ],
        "join_relations": [
            {"join_condition": "fact_order_item.product_id = dim_product.product_id"}
        ],
    }
    spec = build_query_spec(
        "统计2025年女装GMV",
        sql,
        {"generation_context": context},
        [{"category_l2": "女装", "gmv": 1}],
    )
    assert spec["metrics"][0]["base_table"] == "fact_order_item"
    assert spec["filters"][0]["value"] == "女装"
    assert spec["dimensions"] == ["p.category_l2"]
    assert spec["time_range"] == {
        "field": "d.date_value",
        "start": "2025-01-01",
        "end": "2026-01-01",
    }
    assert spec["result_summary"] == {
        "row_count": 1,
        "columns": ["category_l2", "gmv"],
    }


def test_query_spec_for_empty_result_records_success_shape_without_fake_columns():
    spec = build_query_spec("q", "SELECT 1", {}, [])
    assert spec["result_summary"] == {"row_count": 0, "columns": []}
