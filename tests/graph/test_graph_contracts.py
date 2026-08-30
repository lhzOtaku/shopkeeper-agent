import pytest

from app.agents.ask_agent.graph import graph as ask_graph
from app.agents.ask_agent.graph import route_after_sql_validation
from app.agents.main_agent.graph import (
    graph as main_graph,
)
from app.agents.main_agent.graph import (
    route_after_entry,
    route_after_rewrite,
    route_by_intent,
)

pytestmark = [pytest.mark.unit, pytest.mark.graph]


def edge_pairs(compiled_graph):
    return {(edge.source, edge.target) for edge in compiled_graph.get_graph().edges}


def test_ask_graph_contains_the_real_fourteen_nodes():
    nodes = set(ask_graph.get_graph().nodes)
    assert {
        "extract_keywords",
        "recall_column",
        "recall_value",
        "recall_metric",
        "merge_retrieved_info",
        "filter_table",
        "filter_metric",
        "select_metric_variant",
        "enrich_generation_context",
        "generate_sql",
        "validate_sql",
        "correct_sql",
        "run_sql",
        "fail_sql_correction",
    } <= nodes
    assert "add_extra_context" not in nodes


def test_ask_graph_fans_out_three_recall_branches_and_fans_in():
    edges = edge_pairs(ask_graph)
    for node in ("recall_column", "recall_value", "recall_metric"):
        assert ("extract_keywords", node) in edges
        assert (node, "merge_retrieved_info") in edges


def test_ask_graph_joins_table_and_metric_filters_before_variant_selection():
    edges = edge_pairs(ask_graph)
    assert ("merge_retrieved_info", "filter_table") in edges
    assert ("merge_retrieved_info", "filter_metric") in edges
    assert ("filter_table", "select_metric_variant") in edges
    assert ("filter_metric", "select_metric_variant") in edges


def test_enriched_context_flows_directly_to_sql_generation():
    edges = edge_pairs(ask_graph)
    assert ("select_metric_variant", "enrich_generation_context") in edges
    assert ("enrich_generation_context", "generate_sql") in edges


def test_sql_correction_has_a_real_back_edge_to_validation():
    assert ("correct_sql", "validate_sql") in edge_pairs(ask_graph)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"error": None}, "run_sql"),
        ({"error": "bad", "correction_count": 2}, "correct_sql"),
        ({"error": "bad", "correction_count": 3}, "fail_sql_correction"),
        ({"error": "unsafe", "sql_safety_blocked": True}, "fail_sql_correction"),
    ],
)
def test_validate_sql_conditional_edge_is_deterministic(state, expected):
    assert route_after_sql_validation(state) == expected


def test_main_graph_always_commits_terminal_state_through_update_memory():
    edges = edge_pairs(main_graph)
    for terminal in (
        "normal_chat",
        "call_ask_agent",
        "respond_clarification",
        "respond_clarification_exhausted",
        "respond_cancelled",
        "respond_rewrite_failed",
        "respond_retry_unavailable",
        "respond_retry_exhausted",
    ):
        assert (terminal, "update_memory") in edges


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("chat", "normal_chat"),
        ("direct_query", "call_ask_agent"),
        ("followup_query", "rewrite_followup"),
    ],
)
def test_three_intents_have_exact_graph_routes(intent, expected):
    assert route_by_intent({"intent": intent}) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("resolved", "call_ask_agent"),
        ("independent", "call_ask_agent"),
        ("clarify", "respond_clarification"),
        ("cancelled", "respond_cancelled"),
        ("chat", "normal_chat"),
    ],
)
def test_five_followup_statuses_have_exact_routes(status, expected):
    assert route_after_rewrite({"rewrite_result": {"status": status}}) == expected


def test_rewrite_error_and_clarification_exhaustion_use_distinct_routes():
    assert route_after_rewrite({"rewrite_error": "invalid"}) == "respond_rewrite_failed"
    assert (
        route_after_rewrite(
            {"rewrite_result": {"status": "clarify", "clarification_exhausted": True}}
        )
        == "respond_clarification_exhausted"
    )


def test_entry_route_is_precomputed_and_not_reclassified():
    assert route_after_entry({"entry_route": "call_ask_agent"}) == "call_ask_agent"
