from dataclasses import asdict

import pytest

from app.agents.ask_agent.metric_resolution import (
    ORDER_REFUND_RATE,
    QUANTITY_REFUND_RATE,
    has_product_context,
    resolve_refund_rate_metrics,
)
from app.agents.ask_agent.nodes.enrich_generation_context import _build_metric_specs, _field_refs
from app.agents.ask_agent.nodes.select_metric_variant import _select_variant
from app.agents.ask_agent.state import MetricInfoState
from tests.semantic.cases import (
    AMBIGUITY_CASES,
    FAILURE_CASES,
    JOIN_RISK_CASES,
    MULTI_CONDITION_CASES,
    MULTITURN_CASES,
    SIMPLE_CASES,
    VARIANT_CASES,
)

pytestmark = [pytest.mark.unit, pytest.mark.semantic]


@pytest.mark.parametrize(
    ("query", "product_context", "expected"),
    [
        ("退款率", False, ORDER_REFUND_RATE),
        ("女装退款率", True, QUANTITY_REFUND_RATE),
        ("按品类看订单退款率", True, ORDER_REFUND_RATE),
        ("按大区看件数退款率", False, QUANTITY_REFUND_RATE),
    ],
)
def test_refund_rate_resolution_uses_real_business_grain(
    query, product_context, expected
):
    result = resolve_refund_rate_metrics(
        query=query,
        llm_selected_names={ORDER_REFUND_RATE},
        available_metric_names={ORDER_REFUND_RATE, QUANTITY_REFUND_RATE},
        product_context=product_context,
    )
    assert result.selected_names == {expected}


def test_both_explicit_refund_rates_are_preserved():
    result = resolve_refund_rate_metrics(
        query="同时看订单退款率和件数退款率",
        llm_selected_names={ORDER_REFUND_RATE},
        available_metric_names={ORDER_REFUND_RATE, QUANTITY_REFUND_RATE},
        product_context=True,
    )
    assert result.selected_names == {ORDER_REFUND_RATE, QUANTITY_REFUND_RATE}


def test_refund_amount_does_not_trigger_rate_resolution():
    result = resolve_refund_rate_metrics(
        query="统计退款金额",
        llm_selected_names={"退款金额"},
        available_metric_names={"退款金额", ORDER_REFUND_RATE},
        product_context=False,
    )
    assert result.selected_names == {"退款金额"}


@pytest.mark.parametrize(
    ("query", "column_id", "expected"),
    [
        ("女装GMV", "dim_product.category_l2", True),
        ("按品类看GMV", "dim_region.region_name", True),
        ("华东GMV", "dim_region.region_name", False),
        ("不要用商品明细", "dim_product.category_l2", False),
        ("直播渠道GMV", "dim_product.category_l1", False),
    ],
)
def test_product_context_requires_explicit_language_or_strong_es_binding(
    query, column_id, expected, entity_factory
):
    value = "女装" if "女装" in query else "食品"
    assert has_product_context(query, [entity_factory.value(value, column_id)]) is expected


@pytest.mark.parametrize(
    ("question", "metric_name", "expected_table", "expected_grain"),
    VARIANT_CASES[:2],
)
def test_gmv_variant_selection_matches_order_or_product_grain(
    question, metric_name, expected_table, expected_grain, entity_factory
):
    metric = entity_factory.metric(metric_name)
    state_metric = MetricInfoState(
        name=metric.name,
        description=metric.description,
        alias=metric.alias,
        default_variant_id=metric.default_variant_id,
        variants=[asdict(v) for v in metric.variants],
    )
    selected = _select_variant(
        state_metric,
        question,
        {"dim_region"} if expected_grain == "order" else {"dim_product"},
        product_context=expected_grain == "order_item",
    )
    assert selected["base_table"] == expected_table
    assert selected["grain"] == expected_grain


def test_variant_selection_rejects_missing_variants():
    with pytest.raises(ValueError, match="has no variants"):
        _select_variant(
            MetricInfoState(name="broken", default_variant_id="x", variants=[]),
            "q",
            set(),
            False,
        )


def test_variant_selection_rejects_unknown_default(entity_factory):
    metric = entity_factory.metric()
    state_metric = MetricInfoState(
        name="GMV",
        default_variant_id="missing",
        variants=[asdict(v) for v in metric.variants],
    )
    with pytest.raises(ValueError, match="unknown default variant"):
        _select_variant(state_metric, "GMV", set(), False)


def test_metric_specs_include_formula_columns_and_default_filter_columns(
    entity_factory,
):
    metric = entity_factory.metric()
    selected = asdict(metric.variants[0])
    specs, required = _build_metric_specs(
        [
            MetricInfoState(
                name="GMV",
                description="sales",
                alias=["销售额"],
                selected_variant=selected,
            )
        ]
    )
    assert specs[0]["selected_variant"]["formula"] == "SUM(fact_order.order_amount)"
    assert {"fact_order.order_amount", "fact_order.order_status"} <= required


def test_field_reference_parser_extracts_only_table_column_pairs():
    assert _field_refs("fact_order.status <> 'cancelled' AND x = 1") == {
        "fact_order.status"
    }


def test_real_question_catalog_meets_required_case_minimums():
    assert len(SIMPLE_CASES) >= 5
    assert len(MULTI_CONDITION_CASES) >= 5
    assert len(MULTITURN_CASES) >= 5
    assert len(VARIANT_CASES) >= 3
    assert len(JOIN_RISK_CASES) >= 3
    assert len(AMBIGUITY_CASES) >= 3
    assert len(FAILURE_CASES) >= 5


@pytest.mark.parametrize("case", SIMPLE_CASES + MULTI_CONDITION_CASES)
def test_each_real_question_case_records_semantic_expectations(case):
    assert case["question"]
    assert case["metrics"]
    assert case["fact_tables"]
    assert case["grain"]
    assert case["key_sql"]
    assert isinstance(case["non_empty"], bool)
    assert case["why"]


@pytest.mark.parametrize("case", JOIN_RISK_CASES)
def test_join_risk_case_states_failure_mode_and_safe_boundary(case):
    assert any(marker in case["risk"] for marker in ("放大", "重复"))
    assert case["boundary"]


@pytest.mark.parametrize("value, expected_columns", AMBIGUITY_CASES)
def test_ambiguity_cases_require_explicit_field_binding(value, expected_columns):
    assert value
    assert len(expected_columns) >= 2
    assert all("." in column_id for column_id in expected_columns)
