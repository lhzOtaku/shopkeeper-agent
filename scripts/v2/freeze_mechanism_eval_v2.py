"""Freeze the composite v2 Gold while retaining immutable v1 evidence."""

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
    "context_holdout_v2_corrections.yaml",
    "multiturn_holdout_v1.yaml",
    "sql_reliability_holdout_v1.yaml",
)
MANIFEST = DATASET_DIR / "manifest_v2.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    records = {}
    for name in FILES:
        path = DATASET_DIR / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("frozen") is not True:
            raise ValueError(f"{name}: frozen must be true")
        items = data.get("cases") or data.get("sequences") or data.get("corrections") or []
        records[name] = {"sha256": sha256(path), "item_count": len(items)}
    correction = yaml.safe_load(
        (DATASET_DIR / "context_holdout_v2_corrections.yaml").read_text(encoding="utf-8")
    )
    if correction["base_sha256"] != records[correction["base_file"]]["sha256"]:
        raise ValueError("v2 correction base hash does not match immutable v1")
    manifest = {
        "version": 2,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "policy": (
            "v1 remains immutable. v2 applies only documented metadata-name corrections; "
            "all evaluation sections must be rerun."
        ),
        "files": records,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
