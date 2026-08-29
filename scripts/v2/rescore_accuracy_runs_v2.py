"""Re-score saved raw runs after a comparator-only alias correction."""

# ruff: noqa: E402,I001

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.v2.accuracy_eval_lib import compare_results, load_yaml, write_json

ACCURACY_DIR = PROJECT_ROOT / "eval" / "accuracy"
RUN_DIR = PROJECT_ROOT / "eval" / "reports" / "accuracy_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args()


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)

    def count(field: str) -> int:
        return sum(bool(item.get(field)) for item in results)
    initial_exec = sum(bool(item.get("initial_assessment", {}).get("execution_success")) for item in results)
    initial_correct = sum(bool(item.get("initial_assessment", {}).get("result_correct")) for item in results)
    final_exec = count("workflow_success")
    final_correct = count("result_equivalent")
    corrected = [item for item in results if item.get("correction_count", 0) > 0]
    recoverable = [item for item in results if not item.get("initial_assessment", {}).get("execution_success")]
    recovered = [item for item in recoverable if item.get("result_equivalent")]
    return {
        "total": total,
        "task_pass_count": count("passed"),
        "task_accuracy": count("passed") / total,
        "result_correct_count": final_correct,
        "result_accuracy": final_correct / total,
        "initial_execution_success_count": initial_exec,
        "initial_execution_success_rate": initial_exec / total,
        "final_execution_success_count": final_exec,
        "final_execution_success_rate": final_exec / total,
        "execution_success_lift": (final_exec - initial_exec) / total,
        "initial_result_correct_count": initial_correct,
        "initial_result_accuracy": initial_correct / total,
        "corrected_case_count": len(corrected),
        "initial_execution_failed_count": len(recoverable),
        "correction_recovered_correct_count": len(recovered),
        "correction_recovery_rate": len(recovered) / len(recoverable) if recoverable else None,
        "mean_duration_seconds": statistics.mean(item["duration_seconds"] for item in results),
    }


def rescore_query(
    result: dict[str, Any],
    expected_rows: list[dict[str, Any]],
    comparator: dict[str, Any],
) -> None:
    comparison = compare_results(expected_rows, result.get("final_rows") or [], comparator)
    result["comparison"] = comparison
    result["result_equivalent"] = comparison["passed"]
    result["passed"] = bool(
        result.get("workflow_success")
        and result.get("route_passed")
        and comparison["passed"]
        and result.get("contract", {}).get("passed", True)
    )
    initial = result.get("initial_assessment") or {}
    if initial.get("execution_success"):
        initial_comparison = compare_results(
            expected_rows,
            initial.get("rows") or [],
            comparator,
        )
        initial["comparison"] = initial_comparison
        initial["result_correct"] = initial_comparison["passed"]


def main() -> None:
    args = parse_args()
    gold = json.loads((ACCURACY_DIR / "gold_results_v1.json").read_text(encoding="utf-8"))
    single_cases = {
        item["id"]: item
        for item in load_yaml(ACCURACY_DIR / "single_turn_holdout_v1.yaml")["cases"]
    }
    multi_cases = {
        (sequence["id"], turn["id"]): turn
        for sequence in load_yaml(ACCURACY_DIR / "multiturn_holdout_v1.yaml")["sequences"]
        for turn in sequence["turns"]
    }

    for input_path in args.paths:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        if raw.get("partial"):
            raise ValueError(f"Cannot rescore partial run: {input_path}")
        payload = deepcopy(raw)
        query_results: list[dict[str, Any]] = []
        for result in payload.get("single_turn") or []:
            case = single_cases[result["id"]]
            rescore_query(result, gold["single_turn"][result["id"]]["rows"], case["comparator"])
            query_results.append(result)
        for sequence in payload.get("multiturn") or []:
            for turn_result in sequence["turns"]:
                case = multi_cases[(sequence["id"], turn_result["id"])]
                if case.get("gold_id"):
                    rescore_query(
                        turn_result,
                        gold["multiturn"][case["gold_id"]]["rows"],
                        case["comparator"],
                    )
                    query_results.append(turn_result)
            sequence["passed"] = all(turn["passed"] for turn in sequence["turns"])

        sequences = payload.get("multiturn") or []
        payload["multiturn_summary"] = {
            "sequence_count": len(sequences),
            "sequence_pass_count": sum(item["passed"] for item in sequences),
            "sequence_accuracy": sum(item["passed"] for item in sequences) / len(sequences),
            "turn_count": sum(len(item["turns"]) for item in sequences),
            "turn_pass_count": sum(turn["passed"] for item in sequences for turn in item["turns"]),
        }
        payload["query_summary"] = aggregate(query_results)
        payload["failure_categories"] = dict(
            Counter(
                "route" if not item.get("route_passed", True)
                else "execution" if not item.get("workflow_success", True)
                else "result" if not item.get("result_equivalent", True)
                else "contract" if not item.get("contract", {}).get("passed", True)
                else "passed"
                for item in query_results
            )
        )
        payload["rescoring"] = {
            "version": "1.1",
            "rescored_at_utc": datetime.now(timezone.utc).isoformat(),
            "reason": "Comparator alias correction: 成功退款金额 -> refund_amount.",
            "raw_report": input_path.name,
            "model_outputs_unchanged": True,
        }
        output_path = input_path.with_name(f"{input_path.stem}_rescored_v1_1.json")
        write_json(output_path, payload)
        print(output_path)


if __name__ == "__main__":
    main()
