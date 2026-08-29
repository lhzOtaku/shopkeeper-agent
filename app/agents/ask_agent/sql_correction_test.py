"""SQL correction-loop test helpers.

The test mode is intentionally implemented around validate_sql: validation can
inject deterministic SQL errors, while correct_sql still exercises the real
repair path.
"""

import os
from typing import Any

from app.core.log import logger

SQL_CORRECTION_TEST_MODE_ENV = "ASK_AGENT_SQL_CORRECTION_TEST_MODE"
SUCCESS_TEST_MODE = "two_bad_then_original"
FAILURE_TEST_MODE = "always_bad"
SUPPORTED_TEST_MODES = {SUCCESS_TEST_MODE, FAILURE_TEST_MODE}


def get_sql_correction_test_mode() -> str:
    """Read optional SQL correction-loop test mode from the environment."""

    return os.getenv(SQL_CORRECTION_TEST_MODE_ENV, "").strip().lower()


def is_sql_correction_test_mode() -> bool:
    """Return True when a supported correction-loop test mode is enabled."""

    return get_sql_correction_test_mode() in SUPPORTED_TEST_MODES


def log_sql_test_block(title: str, lines: list[str]) -> None:
    """Write a visually separated SQL test block to backend logs only."""

    logger.info(f"\n--\n{title}\n{chr(10).join(lines)}\n--")


def normalize_sql(sql: str | None) -> str:
    """Normalize whitespace for simple SQL equality comparison in logs."""

    if not sql:
        return ""
    return " ".join(sql.strip().rstrip(";").split()).lower()


def _strip_sql_for_subquery(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _build_injected_sql(sql: str, validation_round: int) -> tuple[str, str]:
    """Return a deterministic invalid SQL and a Chinese error type label."""

    base_sql = _strip_sql_for_subquery(sql)
    alias = "ask_agent_sql_validation_test"
    if validation_round % 2 == 1:
        return (
            "SELECT * FROM "
            f"({base_sql}) AS {alias} "
            f"ORDER BY __ask_agent_missing_order_column_{validation_round}",
            "不存在的排序字段",
        )

    return (
        "SELECT * FROM "
        f"({base_sql}) AS {alias} "
        f"WHERE __ask_agent_missing_filter_column_{validation_round} = 1",
        "不存在的过滤字段",
    )


def inject_sql_for_validation(
    sql: str,
    validation_round: int,
) -> tuple[str, str | None]:
    """Maybe inject an invalid SQL before validation.

    two_bad_then_original: inject errors in validation rounds 1 and 2, then stop.
    always_bad: inject errors in every validation round until the graph fails.
    """

    mode = get_sql_correction_test_mode()
    if not mode:
        return sql, None

    if mode not in SUPPORTED_TEST_MODES:
        logger.warning(f"未知 SQL 校正测试模式：{mode}，将按正常校验流程执行。")
        return sql, None

    if mode == SUCCESS_TEST_MODE and validation_round >= 3:
        return sql, None

    injected_sql, injection_type = _build_injected_sql(sql, validation_round)
    return injected_sql, injection_type


def log_sql_correction_summary(state: dict[str, Any], title: str) -> None:
    """Log validation/correction records for correction-loop tests."""

    if not is_sql_correction_test_mode():
        return

    original_sql = state.get("original_sql") or ""
    final_sql = state.get("sql") or ""
    validation_records = state.get("sql_validation_records") or []
    correction_records = state.get("sql_correction_records") or []

    lines = [
        f"测试模式：{get_sql_correction_test_mode()}",
        f"原始生成SQL：{original_sql}",
        f"最终状态SQL：{final_sql}",
        f"最终SQL是否与原始SQL一致：{normalize_sql(final_sql) == normalize_sql(original_sql)}",
        f"校验次数：{len(validation_records)}",
        f"校正次数：{len(correction_records)}",
        "校验记录：",
    ]

    for record in validation_records:
        lines.extend(
            [
                f"第 {record.get('validation_round')} 次校验：{record.get('status')}",
                f"  校正次数快照：{record.get('correction_count')}",
                f"  校验前SQL：{record.get('candidate_sql')}",
                f"  注入错误类型：{record.get('injection_type') or '无'}",
                f"  实际校验SQL：{record.get('validated_sql')}",
                f"  错误原因：{record.get('error') or '无'}",
            ]
        )

    lines.append("校正记录：")
    for record in correction_records:
        corrected_sql = record.get("corrected_sql") or ""
        lines.extend(
            [
                f"第 {record.get('correction_count')} 次校正：",
                f"  校正前SQL：{record.get('before_sql')}",
                f"  依据错误：{record.get('error')}",
                f"  校正后SQL：{corrected_sql}",
                "  校正方式：correct_sql 节点结合错误信息和上下文生成修正 SQL，"
                "具体变化以上一行前后 SQL 对比为准。",
                f"  校正后是否与原始SQL一致：{normalize_sql(corrected_sql) == normalize_sql(original_sql)}",
            ]
        )

    log_sql_test_block(title, lines)
