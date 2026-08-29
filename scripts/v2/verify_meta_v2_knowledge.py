"""Verify the rebuilt v2 knowledge stores without modifying them."""

import asyncio
import sys
from pathlib import Path
from typing import Any

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.es_client_manager import es_client_manager  # noqa: E402
from app.clients.mysql_client_manager import meta_mysql_client_manager  # noqa: E402
from app.clients.qdrant_client_manager import qdrant_client_manager  # noqa: E402
from app.conf.app_config import app_config  # noqa: E402


async def mysql_counts() -> dict[str, Any]:
    meta_mysql_client_manager.init()
    try:
        async with meta_mysql_client_manager.session_factory() as session:
            counts: dict[str, Any] = {}
            for table_name in [
                "table_info",
                "column_info",
                "table_relation",
                "metric_info",
                "metric_variant",
                "metric_variant_column",
            ]:
                result = await session.execute(
                    text(f"select count(*) from {table_name}")
                )
                counts[table_name] = result.scalar()

            result = await session.execute(
                text("select grain from table_info where id = 'fact_order'")
            )
            counts["fact_order.grain"] = result.scalar()

            result = await session.execute(
                text(
                    "select mi.default_variant_id, mv.formula, mv.grain "
                    "from metric_info mi "
                    "join metric_variant mv on mv.id = mi.default_variant_id "
                    "where mi.id = 'GMV'"
                )
            )
            gmv_metric = result.mappings().first()
            counts["GMV.default_variant_id"] = (
                gmv_metric["default_variant_id"] if gmv_metric else None
            )
            counts["GMV.default_formula"] = (
                gmv_metric["formula"] if gmv_metric else None
            )
            counts["GMV.default_grain"] = gmv_metric["grain"] if gmv_metric else None

            result = await session.execute(
                text(
                    "select count(*) from metric_info mi "
                    "left join metric_variant mv on mv.id = mi.default_variant_id "
                    "where mv.id is null"
                )
            )
            counts["invalid_default_variant_count"] = result.scalar()

            result = await session.execute(
                text(
                    "select count(*) from information_schema.tables "
                    "where table_schema = database() and table_name = 'column_metric'"
                )
            )
            counts["legacy_column_metric_table_count"] = result.scalar()

            result = await session.execute(
                text(
                    "select left_table, left_column, right_table, right_column "
                    "from table_relation order by id limit 3"
                )
            )
            counts["sample_relations"] = [dict(row) for row in result.mappings()]

            if counts["invalid_default_variant_count"]:
                raise RuntimeError("Meta contains metrics without a valid default variant")
            if counts["legacy_column_metric_table_count"]:
                raise RuntimeError("Legacy column_metric table still exists")
            return counts
    finally:
        await meta_mysql_client_manager.close()


async def qdrant_counts() -> dict[str, Any]:
    qdrant_client_manager.init()
    client: AsyncQdrantClient = qdrant_client_manager.client
    try:
        counts: dict[str, Any] = {}
        for collection_name in [
            app_config.qdrant.column_collection_name,
            app_config.qdrant.metric_collection_name,
        ]:
            exists = await client.collection_exists(collection_name)
            if not exists:
                counts[collection_name] = 0
                continue
            result = await client.count(collection_name=collection_name, exact=True)
            counts[collection_name] = result.count

        metric_collection = app_config.qdrant.metric_collection_name
        if counts.get(metric_collection, 0):
            points, _ = await client.scroll(
                collection_name=metric_collection,
                limit=1,
                with_payload=True,
                with_vectors=False,
            )
            payload = points[0].payload or {} if points else {}
            counts["metric_payload_keys"] = sorted(payload)
            counts["metric_payload_has_formula"] = "formula" in payload
            counts["metric_payload_has_variants"] = "variants" in payload
            counts["metric_payload_has_relevant_columns"] = (
                "relevant_columns" in payload
            )
            if any(
                counts[key]
                for key in [
                    "metric_payload_has_formula",
                    "metric_payload_has_variants",
                    "metric_payload_has_relevant_columns",
                ]
            ):
                raise RuntimeError("Metric Qdrant payload contains executable metadata")
        return counts
    finally:
        await qdrant_client_manager.close()


async def es_counts_and_samples() -> dict[str, Any]:
    es_client_manager.init()
    client: AsyncElasticsearch = es_client_manager.client
    try:
        index_name = app_config.es.index_name
        exists = await client.indices.exists(index=index_name)
        if not exists:
            return {"index": index_name, "exists": False, "count": 0, "samples": {}}

        count = (await client.count(index=index_name))["count"]
        samples: dict[str, list[dict[str, Any]]] = {}
        for keyword in ["直播", "女装", "华东"]:
            resp = await client.search(
                index=index_name,
                query={"match": {"value": keyword}},
                size=3,
            )
            samples[keyword] = [hit["_source"] for hit in resp["hits"]["hits"]]

        return {
            "index": index_name,
            "exists": True,
            "count": count,
            "samples": samples,
        }
    finally:
        await es_client_manager.close()


async def main() -> None:
    print("MySQL:", await mysql_counts())
    print("Qdrant:", await qdrant_counts())
    print("Elasticsearch:", await es_counts_and_samples())


if __name__ == "__main__":
    asyncio.run(main())
