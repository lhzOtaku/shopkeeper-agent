"""Shared utilities for the held-out Text2SQL accuracy evaluation."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

GLOBAL_COLUMN_ALIASES = {
    "gmv": {"gmv", "销售额", "成交额", "订单金额", "商品gmv", "item_gmv", "order_gmv"},
    "pay_amount": {"pay_amount", "实付金额", "支付金额", "实收金额", "实际支付"},
    "order_count": {"order_count", "订单数", "有效订单数"},
    "paid_member_count": {"paid_member_count", "支付用户数", "购买用户数", "成交用户数"},
    "aov": {"aov", "客单价"},
    "member_avg_amount": {"member_avg_amount", "人均消费", "人均成交额", "人均gmv"},
    "sales_quantity": {"sales_quantity", "销量", "销售件数", "quantity"},
    "item_unit_price": {"item_unit_price", "件单价", "平均件单价"},
    "refund_amount": {
        "refund_amount",
        "退款金额",
        "成功退款金额",
        "退款总额",
        "售后金额",
    },
    "refund_order_count": {"refund_order_count", "退款订单数", "成功退款订单数"},
    "refund_quantity": {"refund_quantity", "退款件数", "退货件数"},
    "order_refund_rate": {"order_refund_rate", "订单退款率", "退款率"},
    "quantity_refund_rate": {"quantity_refund_rate", "件数退款率", "商品退款率"},
    "region_name": {"region_name", "大区", "地区", "区域"},
    "channel_name": {"channel_name", "渠道", "来源渠道"},
    "category_l1": {"category_l1", "一级品类", "品类"},
    "category_l2": {"category_l2", "二级品类", "子品类", "分类"},
    "brand": {"brand", "品牌"},
    "price_band": {"price_band", "价格带", "价格档位"},
    "gender": {"gender", "性别"},
    "month": {"month", "月份", "月"},
    "province": {"province", "省份", "省"},
    "city_level": {"city_level", "城市等级", "城市级别"},
    "refund_reason": {"refund_reason", "退款原因", "售后原因"},
}


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_column_name(name: Any) -> str:
    text = str(name).strip().lower().replace(" `", "").replace("`", "")
    for canonical, aliases in GLOBAL_COLUMN_ALIASES.items():
        if text in {alias.lower() for alias in aliases}:
            return canonical
    return text


def canonicalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {normalize_column_name(key): json_value(value) for key, value in row.items()}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def values_equal(expected: Any, actual: Any, tolerance: Decimal) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    expected_number = _decimal(expected)
    actual_number = _decimal(actual)
    if expected_number is not None and actual_number is not None:
        return abs(expected_number - actual_number) <= tolerance
    return str(expected).strip() == str(actual).strip()


def _project_rows(rows: list[dict[str, Any]], columns: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    projected: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw_row in enumerate(rows):
        row = canonicalize_row(raw_row)
        missing = [column for column in columns if column not in row]
        if missing:
            errors.append(f"row {index} missing columns: {missing}; actual={sorted(row)}")
            continue
        projected.append({column: row[column] for column in columns})
    return projected, errors


def compare_results(
    expected_rows: list[dict[str, Any]],
    actual_rows: list[dict[str, Any]],
    comparator: dict[str, Any],
) -> dict[str, Any]:
    """Compare result values without requiring generated SQL string equality."""

    mode = comparator["mode"]
    columns = [normalize_column_name(item) for item in comparator.get("columns", [])]
    tolerance = Decimal(str(comparator.get("tolerance", "0.01")))
    expected, expected_errors = _project_rows(expected_rows, columns)
    actual, actual_errors = _project_rows(actual_rows, columns)
    errors = expected_errors + actual_errors
    if errors:
        return {"passed": False, "errors": errors, "expected": expected, "actual": actual}

    if mode == "empty":
        passed = not actual
        if not passed:
            errors.append(f"expected empty result, got {len(actual)} rows")
    elif mode in {"scalar", "row"}:
        passed = len(expected) == len(actual) == 1
        if not passed:
            errors.append(f"expected one row, got expected={len(expected)} actual={len(actual)}")
        elif any(
            not values_equal(expected[0][column], actual[0][column], tolerance)
            for column in columns
        ):
            passed = False
            errors.append("scalar/row values differ")
    elif mode in {"unordered", "ordered"}:
        key_columns = [normalize_column_name(item) for item in comparator.get("key_columns", [])]
        if mode == "ordered":
            pairs = list(zip(expected, actual, strict=False))
            passed = len(expected) == len(actual) and all(
                all(values_equal(left[column], right[column], tolerance) for column in columns)
                for left, right in pairs
            )
        else:
            def key(row: dict[str, Any]) -> tuple[str, ...]:
                return tuple(str(row[column]) for column in key_columns)

            expected_map = {key(row): row for row in expected}
            actual_map = {key(row): row for row in actual}
            passed = expected_map.keys() == actual_map.keys() and all(
                all(values_equal(row[column], actual_map[row_key][column], tolerance) for column in columns)
                for row_key, row in expected_map.items()
            )
        if not passed:
            errors.append(f"{mode} row set differs")
    else:
        raise ValueError(f"Unsupported comparator mode: {mode}")

    return {"passed": passed, "errors": errors, "expected": expected, "actual": actual}
