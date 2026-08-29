from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


@pytest_asyncio.fixture
async def real_repositories():
    """Create real clients, verify health, and skip with an explicit reason."""

    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    meta_session = meta_mysql_client_manager.session_factory()
    dw_session = dw_mysql_client_manager.session_factory()
    try:
        try:
            await meta_session.execute(text("SELECT 1"))
            await dw_session.execute(text("SELECT 1"))
            await qdrant_client_manager.client.get_collections()
            await es_client_manager.client.info()
            embedding = await embedding_client_manager.client.aembed_query("健康检查")
            if not embedding:
                raise RuntimeError("embedding service returned an empty vector")
        except Exception as exc:
            pytest.skip(f"real dependency health check failed: {type(exc).__name__}: {exc}")

        yield SimpleNamespace(
            meta=MetaMySQLRepository(meta_session),
            dw=DWMySQLRepository(dw_session),
            qdrant=qdrant_client_manager.client,
            es=es_client_manager.client,
            embedding=embedding_client_manager.client,
            columns=ColumnQdrantRepository(qdrant_client_manager.client),
            metrics=MetricQdrantRepository(qdrant_client_manager.client),
            values=ValueESRepository(es_client_manager.client),
        )
    finally:
        await meta_session.close()
        await dw_session.close()
        if meta_mysql_client_manager.engine is not None:
            await meta_mysql_client_manager.close()
        if dw_mysql_client_manager.engine is not None:
            await dw_mysql_client_manager.close()
        if qdrant_client_manager.client is not None:
            await qdrant_client_manager.close()
        if es_client_manager.client is not None:
            await es_client_manager.close()
