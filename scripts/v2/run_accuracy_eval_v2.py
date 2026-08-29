"""Run held-out single-turn and multi-turn Text2SQL result accuracy evaluation."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.ask_agent.sql_guard import validate_sql_safety
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.memory.memory_store import memory_store
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.chat_service import ChatService
from scripts.v2.accuracy_eval_lib import (
    compare_results,
    load_yaml,
    sha256_file,
    write_json,
)

ACCURACY_DIR = PROJECT_ROOT / "eval" / "accuracy"
REPORT_DIR = PROJECT_ROOT / "eval" / "reports" / "accuracy_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("calibration", "single", "multi", "all"), default="all")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--sequence-id", action="append", default=[])
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def decode_sse(data: str) -> dict[str, Any]:
    payload = data.removeprefix("data: ").strip()
    return json.loads(payload)


def strip_sql_fence(sql: str) -> str:
    value = sql.strip()
    if value.startswith("```"):
        lines = value.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def collect_event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    intents = [event for event in events if event.get("type") == "intent"]
    rewrites = [event for event in events if event.get("type") == "followup_rewrite"]
    sql_events = [event.get("sql") for event in events if event.get("type") == "tool_sql" and event.get("sql")]
    results = [
        event.get("data")
        for event in events
        if event.get("type") == "tool_result" and event.get("tool") == "askAgent"
    ]
    return {
        "intent": intents[-1].get("intent") if intents else None,
        "intent_source": intents[-1].get("source") if intents else None,
        "rewrite_status": rewrites[-1].get("status") if rewrites else None,
        "resolved_query": rewrites[-1].get("resolved_query") if rewrites else None,
        "sql_events": sql_events,
        "ask_result": results[-1] if results else None,
        "event_types": [event.get("type") for event in events],
        "errors": [event for event in events if event.get("type") in {"error", "ask_failure", "rewrite_failed"}],
    }


def check_contract(query_spec: dict[str, Any] | None, contract: dict[str, Any] | None) -> dict[str, Any]:
    contract = contract or {}
    if not contract:
        return {"passed": True, "errors": []}
    if not query_spec:
        return {"passed": False, "errors": ["query_spec missing"]}
    metrics = query_spec.get("metrics") or []
    names = {str(item.get("name")) for item in metrics if isinstance(item, dict)}
    base_tables = {
        str(item.get("base_table"))
        for item in metrics
        if isinstance(item, dict) and item.get("base_table")
    }
    errors: list[str] = []
    missing_metrics = set(contract.get("metrics") or []) - names
    if missing_metrics:
        errors.append(f"query_spec missing metrics: {sorted(missing_metrics)}; actual={sorted(names)}")
    missing_tables = set(contract.get("base_tables") or []) - base_tables
    if missing_tables:
        errors.append(f"query_spec missing base tables: {sorted(missing_tables)}; actual={sorted(base_tables)}")
    return {"passed": not errors, "errors": errors, "metrics": sorted(names), "base_tables": sorted(base_tables)}


async def assess_initial_sql(
    repo: DWMySQLRepository,
    sql: str | None,
    expected_rows: list[dict[str, Any]] | None,
    comparator: dict[str, Any] | None,
) -> dict[str, Any]:
    if not sql:
        return {"execution_success": False, "result_correct": False, "error": "initial SQL missing"}
    cleaned = strip_sql_fence(sql)
    guard = validate_sql_safety(cleaned)
    if not guard.ok:
        return {
            "execution_success": False,
            "result_correct": False,
            "safety": guard.to_dict(),
            "error": guard.error,
        }
    try:
        await repo.validate(cleaned)
        rows = await repo.run(cleaned)
    except Exception as exc:
        return {
            "execution_success": False,
            "result_correct": False,
            "safety": guard.to_dict(),
            "error": f"{type(exc).__name__}: {exc}",
        }
    comparison = (
        compare_results(expected_rows or [], rows, comparator)
        if comparator is not None and expected_rows is not None
        else None
    )
    return {
        "execution_success": True,
        "result_correct": comparison["passed"] if comparison else None,
        "safety": guard.to_dict(),
        "rows": rows,
        "comparison": comparison,
        "error": None,
    }


async def run_chat_turn(
    service: ChatService,
    session_id: str,
    message: str,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    events = [decode_sse(item) async for item in service.chat(session_id, message)]
    return events, time.perf_counter() - started


async def evaluate_query_turn(
    service: ChatService,
    repo: DWMySQLRepository,
    session_id: str,
    message: str,
    case: dict[str, Any],
    gold: dict[str, Any],
) -> dict[str, Any]:
    events, duration = await run_chat_turn(service, session_id, message)
    summary = collect_event_summary(events)
    ask_result = summary["ask_result"] or {}
    expected_rows = gold["rows"]
    comparison = compare_results(expected_rows, ask_result.get("rows") or [], case["comparator"])
    contract = check_contract(ask_result.get("query_spec"), case.get("contract"))
    initial = await assess_initial_sql(
        repo,
        summary["sql_events"][0] if summary["sql_events"] else None,
        expected_rows,
        case["comparator"],
    )
    route_errors: list[str] = []
    expected_intent = case.get("expected_intent")
    if expected_intent and summary["intent"] != expected_intent:
        route_errors.append(f"intent expected={expected_intent} actual={summary['intent']}")
    expected_rewrite = case.get("expected_rewrite_status")
    if expected_rewrite and summary["rewrite_status"] != expected_rewrite:
        route_errors.append(
            f"rewrite expected={expected_rewrite} actual={summary['rewrite_status']}"
        )
    workflow_success = bool(ask_result.get("success") and ask_result.get("query_spec"))
    passed = workflow_success and comparison["passed"] and contract["passed"] and not route_errors
    return {
        "passed": passed,
        "result_equivalent": comparison["passed"],
        "workflow_success": workflow_success,
        "route_passed": not route_errors,
        "route_errors": route_errors,
        "comparison": comparison,
        "contract": contract,
        "intent": summary["intent"],
        "intent_source": summary["intent_source"],
        "rewrite_status": summary["rewrite_status"],
        "resolved_query": summary["resolved_query"],
        "initial_sql": summary["sql_events"][0] if summary["sql_events"] else None,
        "final_sql": summary["sql_events"][-1] if summary["sql_events"] else ask_result.get("sql"),
        "sql_event_count": len(summary["sql_events"]),
        "correction_count": max(0, len(summary["sql_events"]) - 1),
        "initial_assessment": initial,
        "final_rows": ask_result.get("rows") or [],
        "query_spec": ask_result.get("query_spec"),
        "event_types": summary["event_types"],
        "errors": summary["errors"],
        "duration_seconds": round(duration, 3),
    }


async def evaluate_non_query_turn(
    service: ChatService,
    session_id: str,
    message: str,
    case: dict[str, Any],
) -> dict[str, Any]:
    events, duration = await run_chat_turn(service, session_id, message)
    summary = collect_event_summary(events)
    errors: list[str] = []
    expected_intent = case.get("expected_intent")
    if expected_intent and summary["intent"] != expected_intent:
        errors.append(f"intent expected={expected_intent} actual={summary['intent']}")
    expected_rewrite = case.get("expected_rewrite_status")
    if expected_rewrite and summary["rewrite_status"] != expected_rewrite:
        errors.append(f"rewrite expected={expected_rewrite} actual={summary['rewrite_status']}")
    terminal = case.get("terminal_event")
    if terminal and terminal not in summary["event_types"]:
        errors.append(f"terminal event missing: {terminal}")
    if summary["ask_result"] is not None:
        errors.append("non-query turn unexpectedly invoked askAgent")
    return {
        "passed": not errors,
        "route_passed": not errors,
        "route_errors": errors,
        "intent": summary["intent"],
        "intent_source": summary["intent_source"],
        "rewrite_status": summary["rewrite_status"],
        "resolved_query": summary["resolved_query"],
        "event_types": summary["event_types"],
        "errors": summary["errors"],
        "duration_seconds": round(duration, 3),
    }


def aggregate_query_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    initial_exec = sum(bool(item.get("initial_assessment", {}).get("execution_success")) for item in results)
    final_exec = sum(bool(item.get("workflow_success")) for item in results)
    initial_correct = sum(bool(item.get("initial_assessment", {}).get("result_correct")) for item in results)
    final_correct = sum(bool(item.get("result_equivalent")) for item in results)
    task_pass = sum(bool(item.get("passed")) for item in results)
    corrected = [item for item in results if item.get("correction_count", 0) > 0]
    recoverable = [item for item in results if not item.get("initial_assessment", {}).get("execution_success")]
    recovered = [item for item in recoverable if item.get("result_equivalent")]
    return {
        "total": total,
        "task_pass_count": task_pass,
        "task_accuracy": task_pass / total if total else None,
        "result_correct_count": final_correct,
        "result_accuracy": final_correct / total if total else None,
        "initial_execution_success_count": initial_exec,
        "initial_execution_success_rate": initial_exec / total if total else None,
        "final_execution_success_count": final_exec,
        "final_execution_success_rate": final_exec / total if total else None,
        "execution_success_lift": (final_exec - initial_exec) / total if total else None,
        "initial_result_correct_count": initial_correct,
        "initial_result_accuracy": initial_correct / total if total else None,
        "corrected_case_count": len(corrected),
        "initial_execution_failed_count": len(recoverable),
        "correction_recovered_correct_count": len(recovered),
        "correction_recovery_rate": len(recovered) / len(recoverable) if recoverable else None,
        "mean_duration_seconds": (
            sum(item.get("duration_seconds", 0) for item in results) / total if total else None
        ),
    }


async def create_runtime() -> tuple[ChatService, DWMySQLRepository, Any, Any]:
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    meta_session = meta_mysql_client_manager.session_factory()
    dw_session = dw_mysql_client_manager.session_factory()
    meta_repo = MetaMySQLRepository(meta_session)
    dw_repo = DWMySQLRepository(dw_session)
    service = ChatService(
        meta_repo,
        embedding_client_manager.client,
        dw_repo,
        ColumnQdrantRepository(qdrant_client_manager.client),
        MetricQdrantRepository(qdrant_client_manager.client),
        ValueESRepository(es_client_manager.client),
    )
    return service, dw_repo, meta_session, dw_session


async def close_runtime(meta_session: Any, dw_session: Any) -> None:
    await meta_session.close()
    await dw_session.close()
    if meta_mysql_client_manager.engine is not None:
        await meta_mysql_client_manager.close()
    if dw_mysql_client_manager.engine is not None:
        await dw_mysql_client_manager.close()
    if qdrant_client_manager.client is not None:
        await qdrant_client_manager.close()
    if es_client_manager.client is not None:
        await es_client_manager.close()


async def main() -> None:
    args = parse_args()
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required for formal accuracy evaluation")
    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or REPORT_DIR / f"accuracy_run_{run_id}.json"
    gold_path = ACCURACY_DIR / "gold_results_v1.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    service, dw_repo, meta_session, dw_session = await create_runtime()
    started_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "suite": args.suite,
        "started_at_utc": started_at.isoformat(),
        "dataset_hashes": {
            path.name: sha256_file(path)
            for path in (
                ACCURACY_DIR / "single_turn_holdout_v1.yaml",
                ACCURACY_DIR / "multiturn_holdout_v1.yaml",
                gold_path,
                ACCURACY_DIR / "manifest_v1.json",
            )
        },
    }
    all_query_results: list[dict[str, Any]] = []
    try:
        if args.suite == "calibration":
            cases = load_yaml(ACCURACY_DIR / "calibration_cases.yaml")["cases"]
            if args.case_id:
                cases = [item for item in cases if item["id"] in set(args.case_id)]
            results = []
            for index, case in enumerate(cases, start=1):
                print(f"[calibration {index}/{len(cases)}] {case['id']} {case['question']}", flush=True)
                session_id = f"accuracy-{run_id}-{case['id']}-{uuid.uuid4()}"
                case_for_eval = {**case, "expected_intent": "direct_query"}
                result = await evaluate_query_turn(
                    service,
                    dw_repo,
                    session_id,
                    case["question"],
                    case_for_eval,
                    gold["single_turn"][case["gold_id"]],
                )
                memory_store.clear(session_id)
                results.append({"id": case["id"], "question": case["question"], **result})
            payload["calibration"] = results
            all_query_results.extend(results)

        if args.suite in {"single", "all"}:
            cases = load_yaml(ACCURACY_DIR / "single_turn_holdout_v1.yaml")["cases"]
            if args.case_id:
                cases = [item for item in cases if item["id"] in set(args.case_id)]
            results = []
            for index, case in enumerate(cases, start=1):
                print(f"[single {index}/{len(cases)}] {case['id']} {case['question']}", flush=True)
                session_id = f"accuracy-{run_id}-{case['id']}-{uuid.uuid4()}"
                case_for_eval = {**case, "expected_intent": "direct_query"}
                result = await evaluate_query_turn(
                    service,
                    dw_repo,
                    session_id,
                    case["question"],
                    case_for_eval,
                    gold["single_turn"][case["id"]],
                )
                memory_store.clear(session_id)
                results.append({"id": case["id"], "category": case["category"], "question": case["question"], **result})
                write_json(output_path, {**payload, "single_turn": results, "partial": True})
            payload["single_turn"] = results
            all_query_results.extend(results)

        if args.suite in {"multi", "all"}:
            sequences = load_yaml(ACCURACY_DIR / "multiturn_holdout_v1.yaml")["sequences"]
            if args.sequence_id:
                sequences = [item for item in sequences if item["id"] in set(args.sequence_id)]
            sequence_results = []
            for sequence_index, sequence in enumerate(sequences, start=1):
                session_id = f"accuracy-{run_id}-{sequence['id']}-{uuid.uuid4()}"
                turns = []
                for turn_index, turn in enumerate(sequence["turns"], start=1):
                    print(
                        f"[multi {sequence_index}/{len(sequences)} turn {turn_index}/{len(sequence['turns'])}] "
                        f"{sequence['id']}.{turn['id']} {turn['message']}",
                        flush=True,
                    )
                    if turn.get("gold_id"):
                        result = await evaluate_query_turn(
                            service,
                            dw_repo,
                            session_id,
                            turn["message"],
                            turn,
                            gold["multiturn"][turn["gold_id"]],
                        )
                        all_query_results.append(result)
                    else:
                        result = await evaluate_non_query_turn(
                            service, session_id, turn["message"], turn
                        )
                    turns.append({"id": turn["id"], "message": turn["message"], **result})
                memory_store.clear(session_id)
                sequence_results.append(
                    {
                        "id": sequence["id"],
                        "category": sequence["category"],
                        "passed": all(item["passed"] for item in turns),
                        "turns": turns,
                    }
                )
                write_json(output_path, {**payload, "multiturn": sequence_results, "partial": True})
            payload["multiturn"] = sequence_results
            payload["multiturn_summary"] = {
                "sequence_count": len(sequence_results),
                "sequence_pass_count": sum(item["passed"] for item in sequence_results),
                "sequence_accuracy": (
                    sum(item["passed"] for item in sequence_results) / len(sequence_results)
                    if sequence_results
                    else None
                ),
                "turn_count": sum(len(item["turns"]) for item in sequence_results),
                "turn_pass_count": sum(
                    turn["passed"] for item in sequence_results for turn in item["turns"]
                ),
            }

        payload["query_summary"] = aggregate_query_results(all_query_results)
        payload["failure_categories"] = dict(
            Counter(
                "route" if not item.get("route_passed", True)
                else "execution" if not item.get("workflow_success", True)
                else "result" if not item.get("result_equivalent", True)
                else "contract" if not item.get("contract", {}).get("passed", True)
                else "passed"
                for item in all_query_results
            )
        )
        payload["partial"] = False
        payload["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        payload["duration_seconds"] = round(
            (datetime.now(timezone.utc) - started_at).total_seconds(), 3
        )
        write_json(output_path, payload)
        print(json.dumps(payload["query_summary"], ensure_ascii=False, indent=2), flush=True)
        if payload.get("multiturn_summary"):
            print(json.dumps(payload["multiturn_summary"], ensure_ascii=False, indent=2), flush=True)
        print(f"Report: {output_path}", flush=True)
    finally:
        await close_runtime(meta_session, dw_session)


if __name__ == "__main__":
    asyncio.run(main())
