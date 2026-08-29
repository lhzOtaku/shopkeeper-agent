"""Validate and execute read-only Gold SQL for the held-out accuracy suite."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.ask_agent.sql_guard import validate_sql_safety
from app.clients.mysql_client_manager import dw_mysql_client_manager
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from scripts.v2.accuracy_eval_lib import load_yaml, sha256_file, write_json

ACCURACY_DIR = PROJECT_ROOT / "eval" / "accuracy"
REPORT_DIR = PROJECT_ROOT / "eval" / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ACCURACY_DIR / "gold_results_v1.json",
    )
    return parser.parse_args()


def expected_ids() -> tuple[set[str], set[str]]:
    single = load_yaml(ACCURACY_DIR / "single_turn_holdout_v1.yaml")
    multi = load_yaml(ACCURACY_DIR / "multiturn_holdout_v1.yaml")
    single_ids = {case["id"] for case in single["cases"]}
    multi_ids = {
        turn["gold_id"]
        for sequence in multi["sequences"]
        for turn in sequence["turns"]
        if turn.get("gold_id")
    }
    return single_ids, multi_ids


async def execute_gold(
    repo: DWMySQLRepository,
    cases: dict[str, str],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for case_id, sql in cases.items():
        guard = validate_sql_safety(sql)
        if not guard.ok:
            raise ValueError(f"Gold {case_id} failed safety: {guard.to_dict()}")
        await repo.validate(sql)
        rows = await repo.run(sql)
        output[case_id] = {
            "sql": sql,
            "rows": rows,
            "row_count": len(rows),
            "safety": guard.to_dict(),
            "explain_passed": True,
        }
    return output


async def database_fingerprint(repo: DWMySQLRepository) -> dict[str, Any]:
    tables = (
        await repo.session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=DATABASE() ORDER BY table_name"
            )
        )
    ).scalars().all()
    row_counts: dict[str, int] = {}
    for table_name in tables:
        row_counts[table_name] = int(
            (await repo.session.execute(text(f"SELECT COUNT(*) FROM `{table_name}`"))).scalar_one()
        )
    date_range = (
        await repo.session.execute(
            text("SELECT MIN(date_value), MAX(date_value), COUNT(*) FROM dim_date")
        )
    ).one()
    return {
        "database": "dw_v2",
        "row_counts": row_counts,
        "date_min": date_range[0],
        "date_max": date_range[1],
        "date_count": date_range[2],
    }


async def main() -> None:
    args = parse_args()
    single_spec_path = ACCURACY_DIR / "gold_sql_single_turn_v1.yaml"
    multi_spec_path = ACCURACY_DIR / "gold_sql_multiturn_v1.yaml"
    single_specs = load_yaml(single_spec_path)["cases"]
    multi_specs = load_yaml(multi_spec_path)["cases"]
    expected_single, expected_multi = expected_ids()
    if set(single_specs) != expected_single:
        raise ValueError(
            f"Single Gold IDs differ: missing={expected_single-set(single_specs)} "
            f"extra={set(single_specs)-expected_single}"
        )
    if set(multi_specs) != expected_multi:
        raise ValueError(
            f"Multi Gold IDs differ: missing={expected_multi-set(multi_specs)} "
            f"extra={set(multi_specs)-expected_multi}"
        )

    dw_mysql_client_manager.init()
    session = dw_mysql_client_manager.session_factory()
    try:
        repo = DWMySQLRepository(session)
        payload = {
            "schema_version": 1,
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_fingerprint": await database_fingerprint(repo),
            "source_hashes": {
                single_spec_path.name: sha256_file(single_spec_path),
                multi_spec_path.name: sha256_file(multi_spec_path),
                "single_turn_holdout_v1.yaml": sha256_file(
                    ACCURACY_DIR / "single_turn_holdout_v1.yaml"
                ),
                "multiturn_holdout_v1.yaml": sha256_file(
                    ACCURACY_DIR / "multiturn_holdout_v1.yaml"
                ),
            },
            "single_turn": await execute_gold(repo, single_specs),
            "multiturn": await execute_gold(repo, multi_specs),
        }
        write_json(args.output, payload)
        manifest = {
            "schema_version": 1,
            "frozen_at_utc": payload["built_at_utc"],
            "files": {
                path.name: sha256_file(path)
                for path in (
                    ACCURACY_DIR / "single_turn_holdout_v1.yaml",
                    ACCURACY_DIR / "multiturn_holdout_v1.yaml",
                    single_spec_path,
                    multi_spec_path,
                    args.output,
                    REPORT_DIR / "accuracy_data_profile_v1.json",
                )
            },
            "database_fingerprint": payload["database_fingerprint"],
        }
        write_json(ACCURACY_DIR / "manifest_v1.json", manifest)
        print(
            f"Gold built: single={len(single_specs)} multi_turns={len(multi_specs)} "
            f"output={args.output}"
        )
    finally:
        await session.close()
        if dw_mysql_client_manager.engine is not None:
            await dw_mysql_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
