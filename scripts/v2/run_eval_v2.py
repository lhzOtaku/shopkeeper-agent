"""
v2 问数评测脚本

读取 eval/v2_questions.yaml，逐条调用 askAgent 图，收集生成 SQL、执行结果和错误信息，
并根据评测集里的 SQL 断言输出 PASS/FAIL 报告。

第一版评测重点是：
1. 问题是否能完整跑通
2. 是否生成 SQL
3. SQL 是否满足 must_include / must_not_include
4. SQL 是否明显违反只读查询约束

后续可以继续扩展召回结果检查、SQL AST 检查和结果数值校验。
"""

# ruff: noqa: E402

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.graph import graph as ask_graph
from app.agents.ask_agent.state import DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.log import logger
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

DEFAULT_EVAL_FILE = PROJECT_ROOT / "eval" / "v2_questions.yaml"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "eval" / "reports"

DENY_SQL_TYPES = {"insert", "update", "delete", "drop", "alter", "truncate", "create"}
READONLY_SQL_STARTERS = ("select", "with")


def normalize_sql(sql: str | None) -> str:
    """把 SQL 规整成便于字符串断言的形式。"""

    if not sql:
        return ""
    return re.sub(r"\s+", " ", sql.strip().lower())


def normalize_fragment(fragment: str) -> str:
    """规整断言片段。"""

    return re.sub(r"\s+", " ", str(fragment).strip().lower())


def contains_fragment(sql: str, fragment: str) -> bool:
    """大小写不敏感地检查 SQL 中是否包含片段。"""

    return normalize_fragment(fragment) in sql


def has_quarter_time_condition(sql: str, quarter: str) -> bool:
    """Treat quarter labels and equivalent 2025 date ranges as equal."""

    quarter_ranges = {
        "q1": {
            "start": ("20250101", "2025-01-01"),
            "end": ("20250331", "2025-03-31", "20250401", "2025-04-01"),
        },
        "q2": {
            "start": ("20250401", "2025-04-01"),
            "end": ("20250630", "2025-06-30", "20250701", "2025-07-01"),
        },
        "q3": {
            "start": ("20250701", "2025-07-01"),
            "end": ("20250930", "2025-09-30", "20251001", "2025-10-01"),
        },
        "q4": {
            "start": ("20251001", "2025-10-01"),
            "end": ("20251231", "2025-12-31", "20260101", "2026-01-01"),
        },
    }
    bounds = quarter_ranges.get(quarter)
    if not bounds:
        return False

    return (
        ("quarter" in sql and quarter in sql)
        or (any(start in sql for start in bounds["start"]) and any(end in sql for end in bounds["end"]))
    )


def has_month_time_condition(sql: str) -> bool:
    """Treat month predicates and equivalent 2025 month date ranges as equal."""

    return (
        "month" in sql
        or bool(re.search(r"2025(0[1-9]|1[0-2])01", sql))
        or bool(re.search(r"2025-(0[1-9]|1[0-2])-01", sql))
    )


def has_date_id_time_condition(sql: str) -> bool:
    """Treat fact-table date_id range filters as equivalent to dim_date joins."""

    return "date_id" in sql and (
        bool(re.search(r"2025(0[1-9]|1[0-2])\d{2}", sql))
        or bool(re.search(r"2025-(0[1-9]|1[0-2])-\d{2}", sql))
    )


def has_average_formula_equivalent(sql: str) -> bool:
    """AVG(x) and SUM(x)/COUNT(...) are both valid for order-level average metrics."""

    return (
        "sum" in sql
        and "count" in sql
        and "order_amount" in sql
        and ("/" in sql or "nullif" in sql)
    )


def contains_fragment_or_equivalent(
    case: dict[str, Any], sql: str, fragment: str
) -> bool:
    """Check direct fragment matching, plus a few business-safe SQL equivalents."""

    if contains_fragment(sql, fragment):
        return True

    normalized_fragment = normalize_fragment(fragment)
    if normalized_fragment in {"q1", "q2", "q3", "q4"}:
        return has_quarter_time_condition(sql, normalized_fragment)
    if normalized_fragment == "month":
        return has_month_time_condition(sql)
    if normalized_fragment == "dim_date":
        return has_date_id_time_condition(sql)
    if normalized_fragment == "avg":
        question = str(case.get("question") or "")
        return "客单价" in question and has_average_formula_equivalent(sql)
    return False


def has_denied_sql_type(sql: str, denied_types: set[str]) -> str | None:
    """检查 SQL 是否明显包含危险写操作。"""

    if not sql:
        return None

    first_token = sql.split(" ", 1)[0]
    if first_token in denied_types:
        return first_token

    for denied_type in denied_types:
        if re.search(rf";\s*{denied_type}\b", sql):
            return denied_type
    return None


def looks_readonly(sql: str) -> bool:
    """检查 SQL 是否以 SELECT 或 WITH 开头。"""

    return sql.startswith(READONLY_SQL_STARTERS)


def has_visible_exclusion(sql: str, value: str) -> bool:
    """
    粗略判断 SQL 是否显式排除了某个值。

    这是字符串级别的第一版检查，只负责发现明显遗漏。
    更严谨的判断应该交给 SQL AST 或执行结果校验。
    """

    value = normalize_fragment(value)
    if value not in sql:
        # 如果 SQL 使用了状态白名单，也可以认为间接排除了某个状态。
        whitelist_patterns = [
            r"order_status\s+in\s*\(",
            r"refund_status\s+in\s*\(",
        ]
        return any(re.search(pattern, sql) for pattern in whitelist_patterns)

    exclusion_patterns = [
        rf"!=\s*['\"]?{re.escape(value)}['\"]?",
        rf"<>\s*['\"]?{re.escape(value)}['\"]?",
        rf"not\s+in\s*\([^)]*{re.escape(value)}[^)]*\)",
        rf"not\s+like\s*['\"]?%?{re.escape(value)}%?['\"]?",
    ]
    return any(re.search(pattern, sql) for pattern in exclusion_patterns)


def check_sql_assertions(
    case: dict[str, Any],
    sql: str | None,
    default_checks: dict[str, Any],
    strict: bool,
) -> tuple[list[str], list[str]]:
    """返回 errors 和 warnings。"""

    errors: list[str] = []
    warnings: list[str] = []
    normalized_sql = normalize_sql(sql)

    if not normalized_sql:
        return ["未生成 SQL"], warnings

    denied_types = set(default_checks.get("deny_sql_types") or DENY_SQL_TYPES)
    denied_type = has_denied_sql_type(normalized_sql, denied_types)
    if denied_type:
        errors.append(f"SQL 包含禁止操作：{denied_type}")

    if default_checks.get("require_readonly", True) and not looks_readonly(
        normalized_sql
    ):
        errors.append("SQL 不是只读查询，应该以 SELECT 或 WITH 开头")

    assertions = case.get("sql_assertions") or {}

    for fragment in assertions.get("must_include") or []:
        if not contains_fragment_or_equivalent(case, normalized_sql, fragment):
            errors.append(f"SQL 缺少必要片段：{fragment}")

    for fragment in assertions.get("must_not_include") or []:
        if contains_fragment(normalized_sql, fragment):
            errors.append(f"SQL 不应包含片段：{fragment}")

    for value in assertions.get("should_exclude") or []:
        if not has_visible_exclusion(normalized_sql, value):
            message = f"SQL 可能没有正确排除：{value}"
            if strict:
                errors.append(message)
            else:
                warnings.append(message)

    if default_checks.get("require_limit_for_topn", True):
        question = case.get("question", "")
        must_include = [normalize_fragment(x) for x in assertions.get("must_include") or []]
        is_topn = "前" in question or "top" in question.lower() or "limit" in must_include
        if is_topn and "limit" not in normalized_sql:
            errors.append("TopN 问题缺少 LIMIT")

    return errors, warnings


async def run_one_case(
    case: dict[str, Any],
    context: DataAgentContext,
    default_checks: dict[str, Any],
    strict: bool,
) -> dict[str, Any]:
    """执行单条评测样本。"""

    case_id = case["id"]
    question = case["question"]
    logger.info(f"开始评测：{case_id} - {question}")

    sql: str | None = None
    rows: list[dict[str, Any]] | None = None
    stream_error: str | None = None
    progress_events: list[dict[str, Any]] = []

    try:
        async for chunk in ask_graph.astream(
            input=DataAgentState(query=question),
            context=context,
            stream_mode="custom",
        ):
            event_type = chunk.get("type")
            if event_type == "progress":
                progress_events.append(chunk)
            elif event_type == "sql":
                sql = chunk.get("sql")
            elif event_type == "result":
                rows = chunk.get("data") or []
            elif event_type == "error":
                stream_error = chunk.get("message") or "askAgent 返回未知错误"
    except Exception as exc:
        stream_error = str(exc)

    errors, warnings = check_sql_assertions(case, sql, default_checks, strict)
    if stream_error:
        errors.append(f"执行异常：{stream_error}")

    passed = not errors
    logger.info(
        f"完成评测：{case_id}，结果：{'PASS' if passed else 'FAIL'}，"
        f"错误数：{len(errors)}，警告数：{len(warnings)}"
    )

    return {
        "id": case_id,
        "group": case.get("group"),
        "difficulty": case.get("difficulty"),
        "question": question,
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "sql": sql,
        "row_count": len(rows or []),
        "rows_preview": (rows or [])[:5],
        "progress_events": progress_events,
    }


def load_eval_cases(eval_file: Path) -> dict[str, Any]:
    """读取评测集文件。"""

    with eval_file.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ValueError(f"评测集格式错误：{eval_file}")
    return data


def filter_cases(
    cases: list[dict[str, Any]],
    case_ids: list[str] | None,
    group: str | None,
    difficulty: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    """按命令行参数筛选评测样本。"""

    selected = cases
    if case_ids:
        target_ids = set(case_ids)
        selected = [case for case in selected if case.get("id") in target_ids]
    if group:
        selected = [case for case in selected if case.get("group") == group]
    if difficulty:
        selected = [case for case in selected if case.get("difficulty") == difficulty]
    if limit is not None:
        selected = selected[:limit]
    return selected


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总评测结果。"""

    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    failed = total - passed
    warnings = sum(len(result["warnings"]) for result in results)
    by_group: dict[str, dict[str, int]] = {}

    for group in sorted({result.get("group") or "unknown" for result in results}):
        group_results = [result for result in results if result.get("group") == group]
        group_passed = sum(1 for result in group_results if result["passed"])
        by_group[group] = {
            "total": len(group_results),
            "passed": group_passed,
            "failed": len(group_results) - group_passed,
        }

    error_counter: Counter[str] = Counter()
    for result in results:
        for error in result["errors"]:
            error_counter[error.split("：", 1)[0]] += 1

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "pass_rate": round(passed / total, 4) if total else 0,
        "by_group": by_group,
        "error_types": dict(error_counter),
    }


def print_summary(summary: dict[str, Any], report_path: Path | None):
    """打印中文评测摘要。"""

    print("\n===== v2 问数评测摘要 =====")
    print(f"总数：{summary['total']}")
    print(f"通过：{summary['passed']}")
    print(f"失败：{summary['failed']}")
    print(f"警告：{summary['warnings']}")
    print(f"通过率：{summary['pass_rate'] * 100:.2f}%")

    if summary["by_group"]:
        print("\n按类别统计：")
        for group, stats in summary["by_group"].items():
            print(
                f"- {group}: {stats['passed']}/{stats['total']} 通过，"
                f"失败 {stats['failed']}"
            )

    if summary["error_types"]:
        print("\n错误类型：")
        for error_type, count in summary["error_types"].items():
            print(f"- {error_type}: {count}")

    if report_path:
        print(f"\n详细报告：{report_path}")


async def run_eval(args: argparse.Namespace):
    """初始化外部依赖并执行评测。"""

    if args.quiet:
        logger.remove()
        logger.add(sys.stderr, level="ERROR", format="{level} | {message}")

    eval_data = load_eval_cases(args.eval_file)
    selected_cases = filter_cases(
        eval_data["cases"],
        args.case_id,
        args.group,
        args.difficulty,
        args.limit,
    )

    if args.list:
        for case in selected_cases:
            print(
                f"{case['id']} [{case.get('group')}/{case.get('difficulty')}] "
                f"{case['question']}"
            )
        return

    if args.dry_run:
        print(f"评测集：{args.eval_file}")
        print(f"选中样本数：{len(selected_cases)}")
        print(dict(Counter(case.get("group") for case in selected_cases)))
        return

    if not selected_cases:
        raise ValueError("没有选中任何评测样本，请检查筛选参数。")

    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()

    try:
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            context = DataAgentContext(
                column_qdrant_repository=ColumnQdrantRepository(
                    qdrant_client_manager.client
                ),
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=MetricQdrantRepository(
                    qdrant_client_manager.client
                ),
                value_es_repository=ValueESRepository(es_client_manager.client),
                meta_mysql_repository=MetaMySQLRepository(meta_session),
                dw_mysql_repository=DWMySQLRepository(dw_session),
            )

            results: list[dict[str, Any]] = []
            for case in selected_cases:
                result = await run_one_case(
                    case,
                    context,
                    eval_data.get("default_checks") or {},
                    args.strict,
                )
                results.append(result)
                if args.fail_fast and not result["passed"]:
                    break
    finally:
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()

    summary = build_summary(results)
    report_path: Path | None = None
    if args.output:
        report_path = args.output
    elif args.write_report:
        DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = DEFAULT_REPORT_DIR / f"v2_eval_report_{timestamp}.json"

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "eval_file": str(args.eval_file),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "results": results,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    print_summary(summary, report_path)

    if summary["failed"]:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="运行 v2 问数 Agent 评测集")
    parser.add_argument(
        "--eval-file",
        type=Path,
        default=DEFAULT_EVAL_FILE,
        help="评测集 YAML 文件路径",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        help="只运行指定 case id，可重复传入",
    )
    parser.add_argument("--group", help="只运行指定 group")
    parser.add_argument("--difficulty", help="只运行指定难度：easy/medium/hard")
    parser.add_argument("--limit", type=int, help="只运行前 N 条样本")
    parser.add_argument("--strict", action="store_true", help="把 warning 也视为失败")
    parser.add_argument("--fail-fast", action="store_true", help="遇到第一条失败就停止")
    parser.add_argument("--write-report", action="store_true", help="写入 JSON 详细报告")
    parser.add_argument("--output", type=Path, help="指定 JSON 报告输出路径")
    parser.add_argument("--quiet", action="store_true", help="只输出评测摘要和 ERROR 日志")
    parser.add_argument("--dry-run", action="store_true", help="只校验评测集和筛选结果")
    parser.add_argument("--list", action="store_true", help="列出选中的评测样本")
    return parser.parse_args()


if __name__ == "__main__":
    if sys.platform == "win32" and hasattr(asyncio, "SelectorEventLoop"):
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(run_eval(parse_args()))
    else:
        asyncio.run(run_eval(parse_args()))
