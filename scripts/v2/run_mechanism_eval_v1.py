"""Run frozen, mechanism-focused evaluation for the resume claims.

The runner verifies SHA-256 hashes before doing any work. Gold files are never
rewritten by this script. Raw outputs and aggregate metrics are stored together.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from langgraph.constants import END, START
from langgraph.graph import StateGraph

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.nodes.correct_sql import correct_sql
from app.agents.ask_agent.nodes.enrich_generation_context import enrich_generation_context
from app.agents.ask_agent.nodes.run_sql import run_sql
from app.agents.ask_agent.nodes.select_metric_variant import select_metric_variant
from app.agents.ask_agent.nodes.validate_sql import validate_sql
from app.agents.ask_agent.state import DataAgentState
from app.agents.ask_agent.adapter import AskAgentAdapter
from app.agents.main_agent.graph import graph as main_graph
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.entities.value_info import ValueInfo
from app.memory.session_memory import SessionMemory
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository

DATASET_DIR = ROOT / "eval" / "mechanism"
REPORT_DIR = ROOT / "eval" / "reports" / "mechanism_runs"
MANIFEST = DATASET_DIR / "manifest_v2.json"
DATASETS = {
    "context": DATASET_DIR / "context_holdout_v1.yaml",
    "multiturn": DATASET_DIR / "multiturn_holdout_v1.yaml",
    "sql": DATASET_DIR / "sql_reliability_holdout_v1.yaml",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen_files() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        actual = sha256(DATASET_DIR / name)
        if actual != expected["sha256"]:
            raise RuntimeError(
                f"Frozen Gold hash mismatch for {name}. Create a new dataset "
                "version and rerun; do not edit v1 after seeing outputs."
            )
    return manifest


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_context_dataset() -> dict[str, Any]:
    data = deepcopy(load_yaml(DATASETS["context"]))
    patch = load_yaml(DATASET_DIR / "context_holdout_v2_corrections.yaml")
    if sha256(DATASETS["context"]) != patch["base_sha256"]:
        raise RuntimeError("Context v2 correction no longer matches immutable v1 base")
    cases = {case["id"]: case for case in data["cases"]}
    for correction in patch["corrections"]:
        target = cases[correction["case_id"]]["required"]
        old = correction["replace_required"]["from"]
        new = correction["replace_required"]["to"]
        matched = False
        for values in target.values():
            if old in values:
                values[values.index(old)] = new
                matched = True
        if not matched:
            raise ValueError(f"Correction source not found: {old}")
    data["version"] = 2
    data["dataset"] = "shopkeeper_context_mechanism_holdout_v2"
    return data


def metric_state(metric: Any) -> dict[str, Any]:
    return {
        "name": metric.name,
        "description": metric.description,
        "alias": metric.alias,
        "default_variant_id": metric.default_variant_id,
        "variants": [asdict(variant) for variant in metric.variants],
    }


async def table_state(
    repo: MetaMySQLRepository, table_id: str, column_ids: list[str]
) -> dict[str, Any]:
    table = await repo.get_table_info_by_id(table_id)
    if table is None:
        raise ValueError(f"Unknown table in frozen dataset: {table_id}")
    columns = []
    for column_id in column_ids:
        column = await repo.get_column_info_by_id(column_id)
        if column is None:
            raise ValueError(f"Unknown column in frozen dataset: {column_id}")
        columns.append(
            {
                "name": column.name,
                "type": column.type,
                "role": column.role,
                "examples": list(column.examples or []),
                "description": column.description,
                "alias": list(column.alias or []),
            }
        )
    return {
        "name": table.name,
        "role": table.role,
        "description": table.description,
        "grain": table.grain,
        "columns": columns,
    }


def normalize_join(value: str) -> str:
    left, right = (part.strip() for part in value.replace(" = ", "=").split("=", 1))
    return "=".join(sorted((left, right)))


def context_elements(
    tables: list[dict[str, Any]],
    generation_context: dict[str, Any] | None = None,
) -> dict[str, set[str]]:
    result = {
        "tables": set(),
        "columns": set(),
        "joins": set(),
        "value_bindings": set(),
        "grains": set(),
    }
    for table in tables:
        name = table["name"]
        result["tables"].add(name)
        if table.get("grain"):
            result["grains"].add(f"{name}={table['grain']}")
        for column in table.get("columns") or []:
            result["columns"].add(f"{name}.{column['name']}")
    if generation_context:
        for relation in generation_context.get("join_relations") or []:
            result["joins"].add(normalize_join(relation["join_condition"]))
        for binding in generation_context.get("value_bindings") or []:
            result["value_bindings"].add(
                f"{binding['value']}->{binding['column_id']}"
            )
    return result


def score_required(
    required: dict[str, list[str]], actual: dict[str, set[str]]
) -> tuple[int, int, dict[str, list[str]]]:
    hits = total = 0
    missing: dict[str, list[str]] = {}
    for group in ("tables", "columns", "joins", "value_bindings", "grains"):
        expected = {
            normalize_join(item) if group == "joins" else item
            for item in required.get(group) or []
        }
        group_missing = sorted(expected - actual[group])
        hits += len(expected) - len(group_missing)
        total += len(expected)
        if group_missing:
            missing[group] = group_missing
    return hits, total, missing


async def run_context_eval(
    meta_repo: MetaMySQLRepository, context: DataAgentContext
) -> dict[str, Any]:
    data = load_context_dataset()
    builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)
    builder.add_node("select_metric_variant", select_metric_variant)
    builder.add_node("enrich_generation_context", enrich_generation_context)
    builder.add_edge(START, "select_metric_variant")
    builder.add_edge("select_metric_variant", "enrich_generation_context")
    builder.add_edge("enrich_generation_context", END)
    graph = builder.compile()

    cases = []
    variant_hits = variant_total = pre_hits = pre_total = post_hits = post_total = 0
    for case in data["cases"]:
        metrics = await meta_repo.get_metric_infos_by_ids([case["metric"]])
        if len(metrics) != 1:
            raise ValueError(f"{case['id']}: metric not found: {case['metric']}")
        columns_by_table: dict[str, list[str]] = {name: [] for name in case["seed_tables"]}
        for column_id in case.get("seed_columns") or []:
            columns_by_table[column_id.split(".", 1)[0]].append(column_id)
        tables = [
            await table_state(meta_repo, name, columns_by_table[name])
            for name in case["seed_tables"]
        ]
        values = []
        for index, item in enumerate(case.get("values") or []):
            table_id, column_name = item["column_id"].split(".", 1)
            values.append(
                ValueInfo(
                    id=f"{case['id']}-V{index + 1}",
                    value=item["value"],
                    column_id=item["column_id"],
                    table_id=table_id,
                    column_name=column_name,
                    column_alias=[],
                    field_role="dimension",
                )
            )
        initial_state = DataAgentState(
            query=case["question"],
            table_infos=tables,
            metric_infos=[metric_state(metrics[0])],
            retrieved_value_infos=values,
        )
        final_state = await graph.ainvoke(initial_state, context=context)
        selected = final_state["metric_infos"][0]["selected_variant"]["name"]
        variant_pass = selected == case["expected_variant"]
        if case.get("score_variant"):
            variant_total += 1
            variant_hits += int(variant_pass)

        pre = context_elements(tables)
        post = context_elements(
            final_state["table_infos"], final_state["generation_context"]
        )
        case_pre_hits, case_total, pre_missing = score_required(case["required"], pre)
        case_post_hits, _, post_missing = score_required(case["required"], post)
        pre_hits += case_pre_hits
        post_hits += case_post_hits
        pre_total += case_total
        post_total += case_total
        cases.append(
            {
                "id": case["id"],
                "question": case["question"],
                "expected_variant": case["expected_variant"],
                "actual_variant": selected,
                "variant_pass": variant_pass,
                "pre_coverage": case_pre_hits / case_total,
                "post_coverage": case_post_hits / case_total,
                "pre_missing": pre_missing,
                "post_missing": post_missing,
                "generation_context": final_state["generation_context"],
            }
        )
    return {
        "dataset": data["dataset"],
        "metrics": {
            "variant_accuracy": variant_hits / variant_total,
            "variant_hits": variant_hits,
            "variant_total": variant_total,
            "metadata_coverage_before": pre_hits / pre_total,
            "metadata_coverage_after": post_hits / post_total,
            "metadata_hits_before": pre_hits,
            "metadata_hits_after": post_hits,
            "metadata_total": post_total,
        },
        "cases": cases,
    }


def query_spec_from_text(query: str) -> dict[str, Any]:
    metrics = [
        name
        for name in (
            "订单退款率",
            "件数退款率",
            "退款金额",
            "订单数",
            "实付金额",
            "GMV",
            "销量",
        )
        if name in query
    ]
    filters = []
    field_map = {
        "抖音": "dim_channel.channel_name",
        "直播": "dim_channel.channel_name",
        "华东": "dim_region.region_name",
        "华南": "dim_region.region_name",
        "女装": "dim_product.category_l2",
    }
    for value, field in field_map.items():
        if value in query:
            filters.append({"field": field, "operator": "=", "value": value})
    year = next((year for year in ("2024", "2025") if year in query), None)
    return {
        "metrics": [{"name": name, "description": name} for name in metrics],
        "tables": [{"name": "fact_order", "description": "订单事实表"}],
        "filters": filters,
        "dimensions": [],
        "time_range": {"year": year} if year else None,
        "sql": "SELECT 1",
    }


def contains_all(text: str | None, values: list[str]) -> bool:
    normalized = text or ""
    return all(str(value) in normalized for value in values)


async def run_multiturn_eval() -> dict[str, Any]:
    data = load_yaml(DATASETS["multiturn"])
    original_stream = AskAgentAdapter.stream
    sequence_results = []
    rewrite_hits = rewrite_total = sequence_hits = 0
    try:
        for sequence in data["sequences"]:
            outcomes = [
                turn["ask_outcome"]
                for turn in sequence["turns"]
                if turn.get("ask_outcome") not in (None, "none")
            ]
            outcome_index = 0

            async def stub_stream(self, query: str):
                nonlocal outcome_index
                if outcome_index >= len(outcomes):
                    raise AssertionError(f"{sequence['id']}: unexpected askAgent call")
                outcome = outcomes[outcome_index]
                outcome_index += 1
                success = outcome == "success"
                yield {
                    "type": "tool_result",
                    "tool": "askAgent",
                    "data": {
                        "success": success,
                        "query": query,
                        "sql": "SELECT 1" if success else None,
                        "rows": [],
                        "query_spec": query_spec_from_text(query) if success else None,
                        "error_type": None if success else "dependency_unavailable",
                        "error_message": None if success else "injected retryable failure",
                        "retryable": not success,
                    },
                }

            AskAgentAdapter.stream = stub_stream
            memory = SessionMemory(sequence["id"])
            context = {
                "memory": memory,
                "column_qdrant_repository": None,
                "embedding_client": None,
                "metric_qdrant_repository": None,
                "value_es_repository": None,
                "meta_mysql_repository": None,
                "dw_mysql_repository": None,
            }
            turns = []
            sequence_pass = True
            baseline_context: dict[str, Any] | None = None
            for turn in sequence["turns"]:
                before_context = deepcopy(memory.last_successful_query_context)
                state = await main_graph.ainvoke(
                    {"session_id": sequence["id"], "message": turn["message"]},
                    context=context,
                )
                rewrite = state.get("rewrite_result") or {}
                effective = state.get("effective_query") or rewrite.get("resolved_query")
                pending_phase = (
                    memory.pending_query.get("phase") if memory.pending_query else "none"
                )
                checks: dict[str, bool] = {}
                for key, actual in (
                    ("expected_intent", state.get("intent")),
                    ("expected_entry_route", state.get("entry_route")),
                    ("expected_rewrite_status", rewrite.get("status")),
                    ("expected_pending_phase", pending_phase),
                ):
                    if key in turn:
                        checks[key] = actual == turn[key]
                required = turn.get("required_concepts") or turn.get(
                    "required_effective_concepts"
                ) or []
                forbidden = turn.get("forbidden_concepts") or turn.get(
                    "forbidden_effective_concepts"
                ) or []
                if required:
                    checks["required_concepts"] = contains_all(effective, required)
                if forbidden:
                    checks["forbidden_concepts"] = not any(
                        str(value) in (effective or "") for value in forbidden
                    )
                if turn.get("expected_success_context") == "preserved":
                    checks["success_context_preserved"] = (
                        memory.last_successful_query_context == before_context
                    )
                if turn.get("score_rewrite"):
                    rewrite_total += 1
                    rewrite_pass = all(checks.values())
                    rewrite_hits += int(rewrite_pass)
                turn_pass = all(checks.values())
                sequence_pass = sequence_pass and turn_pass
                if memory.last_successful_query_context and baseline_context is None:
                    baseline_context = deepcopy(memory.last_successful_query_context)
                turns.append(
                    {
                        "message": turn["message"],
                        "entry_route": state.get("entry_route"),
                        "intent": state.get("intent"),
                        "rewrite_result": rewrite,
                        "effective_query": effective,
                        "pending_phase": pending_phase,
                        "checks": checks,
                        "pass": turn_pass,
                    }
                )
            sequence_hits += int(sequence_pass)
            sequence_results.append(
                {
                    "id": sequence["id"],
                    "category": sequence["category"],
                    "pass": sequence_pass,
                    "turns": turns,
                }
            )
    finally:
        AskAgentAdapter.stream = original_stream
    return {
        "dataset": data["dataset"],
        "metrics": {
            "rewrite_accuracy": rewrite_hits / rewrite_total,
            "rewrite_hits": rewrite_hits,
            "rewrite_total": rewrite_total,
            "sequence_accuracy": sequence_hits / len(data["sequences"]),
            "sequence_hits": sequence_hits,
            "sequence_total": len(data["sequences"]),
        },
        "sequences": sequence_results,
    }


async def build_sql_state(
    case: dict[str, Any], meta_repo: MetaMySQLRepository
) -> DataAgentState:
    columns_by_table: dict[str, list[str]] = {
        table: [] for table in case["allowed_tables"]
    }
    for column_id in case.get("allowed_columns") or []:
        table = column_id.split(".", 1)[0]
        columns_by_table.setdefault(table, []).append(column_id)
    tables = [
        await table_state(meta_repo, table, columns_by_table.get(table, []))
        for table in case["allowed_tables"]
    ]
    generation_context = {
        "metrics": [],
        "tables": tables,
        "value_bindings": [],
        "join_relations": [],
    }
    return DataAgentState(
        query=case["query"],
        table_infos=tables,
        metric_infos=[],
        generation_context=generation_context,
        date_info={"date": "2026-08-09", "weekday": "Sunday", "quarter": "Q3"},
        db_info={"dialect": "MySQL", "version": "8"},
        sql=case["sql"],
        error=None,
        correction_count=0,
        sql_validation_records=[],
        sql_correction_records=[],
        sql_safety_records=[],
        sql_safety_blocked=False,
    )


def sql_terminal(state: DataAgentState, path: list[str]) -> str:
    if "run_sql" in path:
        return (
            "repaired_and_executed"
            if state.get("correction_count", 0) > 0
            else "executed"
        )
    if state.get("sql_safety_blocked"):
        return (
            "blocked_after_correction"
            if state.get("correction_count", 0) > 0
            else "blocked"
        )
    return "correction_limit"


def build_reliability_graph(case: dict[str, Any]):
    async def eval_correct(state, runtime):
        mode = case.get("correction_mode")
        if not mode:
            return await correct_sql(state, runtime)
        count = state.get("correction_count", 0) + 1
        if mode == "dangerous_after_correction":
            sql = "DELETE FROM fact_order"
        elif mode == "empty_after_correction":
            sql = ""
        else:
            sql = state["sql"]
        return {
            "sql": sql,
            "error": None,
            "correction_count": count,
            "sql_correction_records": [
                *(state.get("sql_correction_records") or []),
                {"round": count, "mode": mode, "corrected_sql": sql},
            ],
        }

    async def eval_fail(state):
        return {}

    def route(state):
        if state.get("sql_safety_blocked"):
            return "eval_fail"
        if state.get("error") is None:
            return "run_sql"
        if state.get("correction_count", 0) < 3:
            return "correct_sql"
        return "eval_fail"

    builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)
    builder.add_node("validate_sql", validate_sql)
    builder.add_node("correct_sql", eval_correct)
    builder.add_node("run_sql", run_sql)
    builder.add_node("eval_fail", eval_fail)
    builder.add_edge(START, "validate_sql")
    builder.add_conditional_edges(
        "validate_sql",
        route,
        {"eval_fail": "eval_fail", "run_sql": "run_sql", "correct_sql": "correct_sql"},
    )
    builder.add_edge("correct_sql", "validate_sql")
    builder.add_edge("run_sql", END)
    builder.add_edge("eval_fail", END)
    return builder.compile()


async def run_sql_eval(
    meta_repo: MetaMySQLRepository, context: DataAgentContext
) -> dict[str, Any]:
    data = load_yaml(DATASETS["sql"])
    results = []
    danger_hits = danger_total = routing_hits = repair_hits = repair_total = 0
    for case in data["cases"]:
        graph = build_reliability_graph(case)
        state = await build_sql_state(case, meta_repo)
        path: list[str] = []
        final_state = state
        try:
            async for update in graph.astream(state, context=context, stream_mode="updates"):
                for node, values in update.items():
                    path.append(node)
                    if values:
                        final_state = {**final_state, **values}
        except Exception as exc:
            path.append("exception")
            final_state = {**final_state, "runner_exception": str(exc)}
        actual = sql_terminal(final_state, path) if "exception" not in path else "exception"
        passed = actual == case["expected_terminal"]
        routing_hits += int(passed)
        if case["category"] == "dangerous":
            danger_total += 1
            danger_hits += int(actual == "blocked" and "run_sql" not in path)
        if case["category"] == "real_repair":
            repair_total += 1
            repair_hits += int(actual == "repaired_and_executed")
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "expected_terminal": case["expected_terminal"],
                "actual_terminal": actual,
                "pass": passed,
                "path": path,
                "initial_sql": case["sql"],
                "final_sql": final_state.get("sql"),
                "correction_count": final_state.get("correction_count", 0),
                "error": final_state.get("error"),
                "safety_blocked": final_state.get("sql_safety_blocked", False),
                "validation_records": final_state.get("sql_validation_records", []),
                "correction_records": final_state.get("sql_correction_records", []),
                "runner_exception": final_state.get("runner_exception"),
            }
        )
    return {
        "dataset": data["dataset"],
        "metrics": {
            "dangerous_block_rate": danger_hits / danger_total,
            "dangerous_hits": danger_hits,
            "dangerous_total": danger_total,
            "routing_accuracy": routing_hits / len(data["cases"]),
            "routing_hits": routing_hits,
            "routing_total": len(data["cases"]),
            "repair_recovery_rate": repair_hits / repair_total,
            "repair_hits": repair_hits,
            "repair_total": repair_total,
        },
        "cases": results,
    }


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_report(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    c = payload.get("context", {}).get("metrics", {})
    m = payload.get("multiturn", {}).get("metrics", {})
    s = payload.get("sql", {}).get("metrics", {})
    lines = [
        "# 电商问数 Agent 机制评测报告 v1",
        "",
        f"- 运行时间：{payload['run_at']}",
        f"- Gold 冻结时间：{payload['manifest']['frozen_at']}",
        "- 防止数据泄漏：运行前校验三套 Gold 的 SHA-256；评测脚本不写回 Gold。",
        "",
        "## 指标口径与上下文增强",
        "",
        f"- 指标变体选择准确率：{pct(c['variant_accuracy'])}（{c['variant_hits']}/{c['variant_total']}）",
        f"- 必要元数据覆盖率：{pct(c['metadata_coverage_before'])} → {pct(c['metadata_coverage_after'])}（micro average）",
        "- 覆盖对象：必要表、字段、显式 Join、字段值绑定和表粒度。",
        "",
        "## 多轮追问",
        "",
        f"- 追问改写准确率：{pct(m['rewrite_accuracy'])}（{m['rewrite_hits']}/{m['rewrite_total']}）",
        f"- 多轮 Sequence Accuracy：{pct(m['sequence_accuracy'])}（{m['sequence_hits']}/{m['sequence_total']}）",
        "- askAgent 在本项中使用确定性桩，避免 SQL 生成波动污染 mainAgent 状态机和改写指标。",
        "",
        "## SQL 可靠执行",
        "",
        f"- 危险 SQL 阻断率：{pct(s['dangerous_block_rate'])}（{s['dangerous_hits']}/{s['dangerous_total']}）",
        f"- 错误路由准确率：{pct(s['routing_accuracy'])}（{s['routing_hits']}/{s['routing_total']}）",
        f"- 可修正错误恢复率：{pct(s['repair_recovery_rate'])}（{s['repair_hits']}/{s['repair_total']}）",
        "- 有效 SQL 和修正后 SQL 均连接真实 dw_v2 执行；危险 SQL 必须在执行前阻断。",
        "",
        "## 边界",
        "",
        "这些指标验证的是局部机制，不等价于开放域 Text2SQL 业务正确率，也不证明对未覆盖分布的泛化能力。",
    ]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main(section: str, output: Path) -> None:
    manifest = verify_frozen_files()
    payload: dict[str, Any] = {
        "version": 2,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
    }
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    try:
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            meta_repo = MetaMySQLRepository(meta_session)
            dw_repo = DWMySQLRepository(dw_session)
            context = DataAgentContext(
                column_qdrant_repository=None,
                embedding_client=None,
                metric_qdrant_repository=None,
                value_es_repository=None,
                meta_mysql_repository=meta_repo,
                dw_mysql_repository=dw_repo,
            )
            if section in {"all", "context"}:
                payload["context"] = await run_context_eval(meta_repo, context)
            if section in {"all", "multiturn"}:
                payload["multiturn"] = await run_multiturn_eval()
            if section in {"all", "sql"}:
                payload["sql"] = await run_sql_eval(meta_repo, context)
    finally:
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
    write_report(payload, output)
    print(json.dumps({key: value.get("metrics") for key, value in payload.items() if isinstance(value, dict) and "metrics" in value}, ensure_ascii=False, indent=2))
    print(f"report={output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", choices=("all", "context", "multiturn", "sql"), default="all")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORT_DIR / "mechanism_eval_v1.json",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args.section, args.output))


if __name__ == "__main__":
    main()
