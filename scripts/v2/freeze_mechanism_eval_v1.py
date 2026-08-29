"""Validate and freeze mechanism-evaluation Gold files before any model run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "eval" / "mechanism"
FILES = (
    "context_holdout_v1.yaml",
    "multiturn_holdout_v1.yaml",
    "sql_reliability_holdout_v1.yaml",
)
MANIFEST = DATASET_DIR / "manifest_v1.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    records: dict[str, dict[str, object]] = {}
    for name in FILES:
        path = DATASET_DIR / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("frozen") is not True:
            raise ValueError(f"{name}: frozen must be true")
        items = data.get("cases") or data.get("sequences") or []
        ids = [item["id"] for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{name}: duplicate ids")
        records[name] = {"sha256": sha256(path), "item_count": len(items)}

    manifest = {
        "version": 1,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "policy": (
            "Gold was authored from metadata/business rules before model execution. "
            "Any Gold change requires a new dataset version and full rerun."
        ),
        "files": records,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
