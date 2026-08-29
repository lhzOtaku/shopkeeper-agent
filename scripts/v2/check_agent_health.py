# ruff: noqa: E402
"""Check v2 Agent dependency health before running evaluations."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.retry import async_retry


async def _check(
    name: str,
    call: Callable[[], Awaitable[Any]],
) -> tuple[str, bool, str]:
    try:
        await async_retry(
            f"health.{name}",
            call,
            attempts=2,
            timeout_seconds=15,
        )
        return name, True, "ok"
    except Exception as exc:
        return name, False, str(exc)


async def _check_mysql(manager, sql: str = "SELECT 1") -> None:
    async with manager.session_factory() as session:
        await session.execute(text(sql))


async def main() -> int:
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    embedding_client_manager.init()

    checks = [
        _check("meta_mysql", lambda: _check_mysql(meta_mysql_client_manager)),
        _check("dw_mysql", lambda: _check_mysql(dw_mysql_client_manager)),
        _check("qdrant", lambda: qdrant_client_manager.client.get_collections()),
        _check("elasticsearch", lambda: es_client_manager.client.info()),
        _check(
            "embedding",
            lambda: embedding_client_manager.client.aembed_query("health check"),
        ),
    ]
    try:
        results = await asyncio.gather(*checks)
    finally:
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()

    failed = 0
    for name, ok, message in results:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {message}")
        if not ok:
            failed += 1

    print(f"\nHealth check: {len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
