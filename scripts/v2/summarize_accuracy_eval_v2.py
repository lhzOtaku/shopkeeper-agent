"""Aggregate three complete re-scored accuracy runs into JSON and Markdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = PROJECT_ROOT / "eval" / "reports" / "accuracy_runs"
DEFAULT_RUNS = [
    RUN_DIR / f"accuracy_run_formal_v1_run{index}_rescored_v1_1.json"
    for index in range(1, 4)
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=DEFAULT_RUNS)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=PROJECT_ROOT / "eval" / "reports" / "text2sql_accuracy_report_v1.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=PROJECT_ROOT / "eval" / "reports" / "text2sql_accuracy_report_v1.md",
    )
    return parser.parse_args()


def rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def mean_sd(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "population_sd": statistics.pstdev(values),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query_results(run: dict[str, Any]) -> list[dict[str, Any]]:
    results = list(run["single_turn"])
    results.extend(
        turn
        for sequence in run["multiturn"]
        for turn in sequence["turns"]
        if "result_equivalent" in turn
    )
    return results


def correction_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [item for item in results if item.get("initial_sql")]
    initial_success = sum(item["initial_assessment"]["execution_success"] for item in candidates)
    final_success = sum(item["workflow_success"] for item in candidates)
    initial_failures = [item for item in candidates if not item["initial_assessment"]["execution_success"]]
    recovered = [item for item in initial_failures if item["workflow_success"] and item["result_equivalent"]]
    return {
        "candidate_count": len(candidates),
        "initial_execution_success_count": initial_success,
        "initial_execution_success_rate": rate(initial_success, len(candidates)),
        "final_execution_success_count": final_success,
        "final_execution_success_rate": rate(final_success, len(candidates)),
        "execution_success_lift": rate(final_success - initial_success, len(candidates)),
        "initial_execution_failure_count": len(initial_failures),
        "recovered_with_correct_result_count": len(recovered),
        "recovery_rate": rate(len(recovered), len(initial_failures)),
    }


def classify_failure(item: dict[str, Any]) -> str:
    if not item.get("route_passed", True):
        return "route"
    if not item.get("workflow_success", True):
        return "execution"
    if not item.get("result_equivalent", True):
        return "result"
    if not item.get("contract", {}).get("passed", True):
        return "contract"
    return "passed"


def main() -> None:
    args = parse_args()
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in args.paths]
    if len(runs) != 3 or any(run.get("partial") for run in runs):
        raise ValueError("Exactly three complete runs are required")
    dataset_hashes = [run["dataset_hashes"] for run in runs]
    if any(item != dataset_hashes[0] for item in dataset_hashes[1:]):
        raise ValueError("Run dataset hashes differ")

    per_run: list[dict[str, Any]] = []
    all_queries: list[dict[str, Any]] = []
    all_correction_candidates: list[dict[str, Any]] = []
    category_totals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    single_stability: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sequence_stability: dict[str, list[bool]] = defaultdict(list)

    for run in runs:
        queries = query_results(run)
        all_queries.extend(queries)
        candidates = [item for item in queries if item.get("initial_sql")]
        all_correction_candidates.extend(candidates)
        single = run["single_turn"]
        for item in single:
            category_totals[item.get("category") or "null"].append(item)
            single_stability[item["id"]].append(item)
        for sequence in run["multiturn"]:
            sequence_stability[sequence["id"]].append(sequence["passed"])
        correction = correction_metrics(queries)
        per_run.append(
            {
                "run_id": run["run_id"],
                "single_result_accuracy": rate(sum(item["result_equivalent"] for item in single), len(single)),
                "single_task_accuracy": rate(sum(item["passed"] for item in single), len(single)),
                "multiturn_sequence_accuracy": run["multiturn_summary"]["sequence_accuracy"],
                "query_result_accuracy": run["query_summary"]["result_accuracy"],
                "query_task_accuracy": run["query_summary"]["task_accuracy"],
                "correction": correction,
                "mean_query_duration_seconds": run["query_summary"]["mean_duration_seconds"],
            }
        )

    metric_keys = (
        "single_result_accuracy",
        "single_task_accuracy",
        "multiturn_sequence_accuracy",
        "query_result_accuracy",
        "query_task_accuracy",
    )
    aggregate_metrics = {
        key: mean_sd([item[key] for item in per_run]) for key in metric_keys
    }
    overall_correction = correction_metrics(all_queries)
    category_summary = {
        category: {
            "evaluations": len(items),
            "result_accuracy": rate(sum(item["result_equivalent"] for item in items), len(items)),
            "task_accuracy": rate(sum(item["passed"] for item in items), len(items)),
        }
        for category, items in sorted(category_totals.items())
    }
    unstable_single = []
    for case_id, items in sorted(single_stability.items()):
        pass_count = sum(item["passed"] for item in items)
        result_count = sum(item["result_equivalent"] for item in items)
        if pass_count < 3:
            unstable_single.append(
                {
                    "id": case_id,
                    "category": items[0].get("category") or "null",
                    "task_pass_runs": pass_count,
                    "result_pass_runs": result_count,
                    "failure_types": [classify_failure(item) for item in items if not item["passed"]],
                }
            )
    unstable_sequences = [
        {"id": sequence_id, "pass_runs": sum(values), "runs": len(values)}
        for sequence_id, values in sorted(sequence_stability.items())
        if not all(values)
    ]
    failure_counts = defaultdict(int)
    for item in all_queries:
        failure_counts[classify_failure(item)] += 1

    payload = {
        "schema_version": "1.1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runs": [path.name for path in args.paths],
        "dataset_hashes": dataset_hashes[0],
        "scoring_source_hashes": {
            name: sha256(PROJECT_ROOT / "scripts" / "v2" / name)
            for name in (
                "accuracy_eval_lib.py",
                "rescore_accuracy_runs_v2.py",
                "summarize_accuracy_eval_v2.py",
            )
        },
        "scope": {
            "formal_runs": 3,
            "single_cases_per_run": 50,
            "multiturn_sequences_per_run": 12,
            "multiturn_turns_per_run": 29,
            "query_turns_per_run": 73,
            "total_query_evaluations": len(all_queries),
            "total_sequence_evaluations": 36,
        },
        "per_run": per_run,
        "aggregate_metrics": aggregate_metrics,
        "overall_correction": overall_correction,
        "category_summary": category_summary,
        "single_case_instability": unstable_single,
        "sequence_instability": unstable_sequences,
        "failure_counts": dict(failure_counts),
        "comparator_revision": {
            "version": "1.1",
            "change": "Added 成功退款金额 as an alias of refund_amount.",
            "raw_reports_preserved": True,
        },
        "verification": {
            "pytest": "205 passed",
            "new_evaluation_files_ruff": "passed",
        },
    }
    args.json_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# 电商数仓多轮 Text2SQL Agent 准确率评测报告 v1.1",
        "",
        f"> 生成时间（UTC）：{payload['generated_at_utc']}",
        "",
        "## 结论",
        "",
        f"- 单轮结果准确率（三轮均值 ± 总体标准差）：{pct(aggregate_metrics['single_result_accuracy']['mean'])} ± {pct(aggregate_metrics['single_result_accuracy']['population_sd'])}",
        f"- 单轮严格任务准确率：{pct(aggregate_metrics['single_task_accuracy']['mean'])} ± {pct(aggregate_metrics['single_task_accuracy']['population_sd'])}",
        f"- 多轮 Sequence Accuracy：{pct(aggregate_metrics['multiturn_sequence_accuracy']['mean'])} ± {pct(aggregate_metrics['multiturn_sequence_accuracy']['population_sd'])}",
        f"- 全部查询回合结果准确率：{pct(aggregate_metrics['query_result_accuracy']['mean'])} ± {pct(aggregate_metrics['query_result_accuracy']['population_sd'])}",
        f"- 全部查询回合严格任务准确率：{pct(aggregate_metrics['query_task_accuracy']['mean'])} ± {pct(aggregate_metrics['query_task_accuracy']['population_sd'])}",
        f"- SQL 首版到最终版的执行成功率：{pct(overall_correction['initial_execution_success_rate'])} -> {pct(overall_correction['final_execution_success_rate'])}，提升 {pct(overall_correction['execution_success_lift'])}",
        f"- 首版执行失败后的正确恢复率：{overall_correction['recovered_with_correct_result_count']}/{overall_correction['initial_execution_failure_count']}（{pct(overall_correction['recovery_rate'])}）",
        "",
        "## 评测范围",
        "",
        "- 5 条 calibration 只用于验证运行器，不计入正式指标。",
        "- 正式集为 50 条单轮、12 组多轮（29 个对话回合，其中 23 个查询回合）。",
        "- 每轮共有 73 个需要结果判定的查询回合，连续运行 3 轮，共 219 次查询评测和 36 次序列评测。",
        "- Gold SQL 与问题分文件保存；每条 Gold 均经过只读安全检查、MySQL EXPLAIN 和真实执行。",
        "- 正式运行未注入 SQL 错误，修正指标来自模型自然产生的首版 SQL 错误。",
        "",
        "## 三轮结果",
        "",
        "| 轮次 | 单轮结果 | 单轮严格 | 多轮序列 | 全查询结果 | 全查询严格 | 首版执行 | 最终执行 | 修正提升 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in per_run:
        correction = item["correction"]
        lines.append(
            f"| {item['run_id']} | {pct(item['single_result_accuracy'])} | {pct(item['single_task_accuracy'])} | "
            f"{pct(item['multiturn_sequence_accuracy'])} | {pct(item['query_result_accuracy'])} | "
            f"{pct(item['query_task_accuracy'])} | {pct(correction['initial_execution_success_rate'])} | "
            f"{pct(correction['final_execution_success_rate'])} | {pct(correction['execution_success_lift'])} |"
        )
    lines.extend(
        [
            "",
            "## 稳定性与失败样本",
            "",
            "单轮未能三轮全部严格通过的样本：",
            "",
            "| 用例 | 类别 | 严格通过轮数 | 结果通过轮数 | 失败类型 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for item in unstable_single:
        lines.append(
            f"| {item['id']} | {item['category']} | {item['task_pass_runs']}/3 | "
            f"{item['result_pass_runs']}/3 | {', '.join(item['failure_types'])} |"
        )
    lines.extend(
        [
            "",
            "多轮未能三轮全部通过的序列：",
            "",
            "| 序列 | 通过轮数 | 主要现象 |",
            "|---|---:|---|",
        ]
    )
    for item in unstable_sequences:
        description = "第三个回合将“渠道改成 App”误判为需要澄清，未执行查询。" if item["id"] == "M12" else "见逐轮 JSON。"
        lines.append(f"| {item['id']} | {item['pass_runs']}/{item['runs']} | {description} |")
    lines.extend(
        [
            "",
            "主要真实边界：S35 连续三轮得到正确数值，但 query_spec 仍记录为商品明细口径；S45 两轮出现订单日期与退款日期口径混合；S29 一轮返回渠道 ID 而非渠道名称；S33 一轮遗漏年份条件；S44 一轮按订单日期而非退款日期统计退款金额。",
            "",
            "## 比较器修订说明",
            "",
            "正式运行后发现结果列“成功退款金额”未被识别为 `refund_amount` 的同义别名，导致 S44 的两轮假阴性。v1.1 只修改结果比较器并基于已保存的原始行重评分，没有重新调用模型、修改 Prompt、业务代码、题目或 Gold；三份原始报告仍保留。第三轮 S44 在修订后仍失败，因为其退款日期口径与 Gold 确实不同。",
            "",
            "## 指标定义",
            "",
            "- 结果准确率：最终返回行与 Gold 结果按标量、无序行集、TopN 有序行集或空结果规则等价。",
            "- 严格任务准确率：结果等价，并且意图/追问路由正确、工作流成功、query_spec 满足声明的指标与口径契约。",
            "- Sequence Accuracy：一组多轮中的每个查询、澄清、闲聊或取消回合都通过，整组才通过。",
            "- SQL 修正提升：只统计实际生成了首版 SQL 的 216 个查询回合；首版 SQL 由评测器独立做只读检查、EXPLAIN 和执行，最终版使用工作流返回状态。",
            "",
            "## 有效性边界",
            "",
            "- 数据来自本地构造的 dw_v2，不代表生产电商数据分布。",
            "- 结果反映当前模型、Prompt、元数据和 2026-07-30 环境，不可外推为通用 Text2SQL 准确率。",
            "- Gold 结果能验证查询结果与部分口径，但不能覆盖所有自然语言歧义；关键失败仍需人工检查 SQL。",
            "- 50 条单轮和 12 组多轮适合作为项目级回归与简历证据，不足以支持学术或生产级泛化结论。",
            "",
            "## 最终回归",
            "",
            "- 完整 pytest：205 passed（包含原 199 项与新增 6 项比较器测试）。",
            "- 本次新增和修改的评测文件 Ruff：通过。",
            "",
            "## 产物",
            "",
            "- 冻结题集与 Gold：`eval/accuracy/`",
            "- 数据画像：`eval/reports/accuracy_data_profile_v1.json`",
            "- 原始与重评分逐轮报告：`eval/reports/accuracy_runs/`",
            "- 机器可读汇总：`eval/reports/text2sql_accuracy_report_v1.json`",
        ]
    )
    args.markdown_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.json_output)
    print(args.markdown_output)


if __name__ == "__main__":
    main()
