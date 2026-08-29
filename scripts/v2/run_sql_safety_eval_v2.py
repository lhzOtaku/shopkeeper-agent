# ruff: noqa: E402
"""Offline SQL safety evaluation for the v2 Text2SQL agent."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.ask_agent.sql_guard import validate_sql_safety

CASE_FILE = ROOT / "eval" / "v2_sql_safety_cases.yaml"


def main() -> int:
    payload = yaml.safe_load(CASE_FILE.read_text(encoding="utf-8"))
    context = payload["context"]
    cases = payload["cases"]

    failures: list[dict[str, object]] = []
    for case in cases:
        result = validate_sql_safety(case["sql"], context)
        expected = case["expected"]
        checks = {
            "ok": result.ok == expected["ok"],
            "blocked": result.blocked == expected["blocked"],
        }
        if "error_type" in expected:
            checks["error_type"] = result.error_type == expected["error_type"]

        passed = all(checks.values())
        status = "PASS" if passed else "FAIL"
        print(
            f"[{status}] {case['name']} "
            f"ok={result.ok} blocked={result.blocked} error_type={result.error_type}"
        )
        if not passed:
            failures.append(
                {
                    "name": case["name"],
                    "expected": expected,
                    "actual": result.to_dict(),
                    "checks": checks,
                }
            )

    passed_count = len(cases) - len(failures)
    print(f"\nSQL safety eval: {passed_count}/{len(cases)} passed")
    if failures:
        print("Failures:")
        for failure in failures:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
