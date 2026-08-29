"""Read-only audit of the four data dependencies used by askAgent.

The script intentionally contains no data-definition or data-mutation operation.
It prints a redacted JSON baseline containing schema, enumerated business values,
aggregate results, and retrieval payload summaries only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.conf.app_config import app_config
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

SAFE_ENUM_COLUMNS = {
    "dim_channel": ("channel_name", "channel_type", "is_paid"),
    "dim_region": ("region_name", "province", "city_level", "country"),
    "dim_product": ("category_l1", "category_l2", "price_band"),
    "fact_order": ("order_status",),
    "fact_refund": ("refund_reason", "refund_status"),
}


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


async def audit_mysql() -> tuple[dict[str, Any], dict[str, Any]]:
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    async with dw_mysql_client_manager.session_factory() as session:
        tables = [
            row[0]
            for row in (
                await session.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() ORDER BY table_name"
                    )
                )
            ).all()
        ]
        columns = _rows(
            await session.execute(
                text(
                    "SELECT table_name, column_name, column_type, column_key "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "ORDER BY table_name, ordinal_position"
                )
            )
        )
        relations = _rows(
            await session.execute(
                text(
                    "SELECT table_name, column_name, referenced_table_name, "
                    "referenced_column_name FROM information_schema.key_column_usage "
                    "WHERE table_schema = DATABASE() "
                    "AND referenced_table_name IS NOT NULL "
                    "ORDER BY table_name, column_name"
                )
            )
        )
        counts: dict[str, int] = {}
        for table_name in tables:
            counts[table_name] = int(
                (
                    await session.execute(
                        text(f"SELECT COUNT(*) FROM `{table_name}`")
                    )
                ).scalar_one()
            )

        date_ranges = _rows(
            await session.execute(
                text(
                    "SELECT MIN(date_value) AS min_date, MAX(date_value) AS max_date, "
                    "COUNT(*) AS date_count FROM dim_date"
                )
            )
        )[0]
        safe_values: dict[str, dict[str, list[Any]]] = {}
        for table_name, column_names in SAFE_ENUM_COLUMNS.items():
            safe_values[table_name] = {}
            for column_name in column_names:
                values = (
                    await session.execute(
                        text(
                            f"SELECT DISTINCT `{column_name}` FROM `{table_name}` "
                            f"WHERE `{column_name}` IS NOT NULL "
                            f"ORDER BY `{column_name}` LIMIT 30"
                        )
                    )
                ).scalars().all()
                safe_values[table_name][column_name] = list(values)

        metric_baseline = _rows(
            await session.execute(
                text(
                    "SELECT COUNT(DISTINCT fo.order_id) AS order_count, "
                    "ROUND(SUM(fo.order_amount), 2) AS order_gmv, "
                    "ROUND(SUM(fo.pay_amount), 2) AS pay_amount, "
                    "ROUND(SUM(fo.order_amount) / NULLIF(COUNT(DISTINCT fo.order_id), 0), 2) AS aov "
                    "FROM fact_order fo JOIN dim_date d ON d.date_id = fo.date_id "
                    "WHERE d.year = 2025 AND fo.order_status <> 'cancelled'"
                )
            )
        )[0]
        item_baseline = _rows(
            await session.execute(
                text(
                    "SELECT p.category_l2, ROUND(SUM(oi.item_amount), 2) AS item_gmv, "
                    "SUM(oi.quantity) AS sales_quantity "
                    "FROM fact_order_item oi "
                    "JOIN fact_order fo ON fo.order_id = oi.order_id "
                    "JOIN dim_product p ON p.product_id = oi.product_id "
                    "JOIN dim_date d ON d.date_id = fo.date_id "
                    "WHERE d.year = 2025 AND fo.order_status <> 'cancelled' "
                    "GROUP BY p.category_l2 ORDER BY item_gmv DESC LIMIT 5"
                )
            )
        )
        refund_baseline = _rows(
            await session.execute(
                text(
                    "SELECT COUNT(DISTINCT r.order_id) AS refund_order_count, "
                    "SUM(r.refund_quantity) AS refund_quantity, "
                    "ROUND(SUM(r.refund_amount), 2) AS refund_amount "
                    "FROM fact_refund r JOIN dim_date d ON d.date_id = r.date_id "
                    "WHERE d.year = 2025 AND r.refund_status = 'success'"
                )
            )
        )[0]
        join_risk = _rows(
            await session.execute(
                text(
                    "SELECT COUNT(*) AS joined_rows, COUNT(DISTINCT fo.order_id) AS orders, "
                    "ROUND(SUM(fo.order_amount), 2) AS duplicated_order_gmv, "
                    "ROUND(SUM(DISTINCT fo.order_amount), 2) AS non_authoritative_distinct_amount "
                    "FROM fact_order fo JOIN fact_order_item oi ON oi.order_id = fo.order_id "
                    "WHERE fo.order_status <> 'cancelled'"
                )
            )
        )[0]
        explain = _rows(
            await session.execute(
                text(
                    "EXPLAIN SELECT d.region_name, SUM(fo.order_amount) AS gmv "
                    "FROM fact_order fo JOIN dim_region d ON d.region_id = fo.region_id "
                    "WHERE fo.order_status <> 'cancelled' "
                    "GROUP BY d.region_name"
                )
            )
        )

    async with meta_mysql_client_manager.session_factory() as session:
        meta_counts = _rows(
            await session.execute(
                text(
                    "SELECT 'table_info' AS object_type, COUNT(*) AS object_count FROM table_info "
                    "UNION ALL SELECT 'column_info', COUNT(*) FROM column_info "
                    "UNION ALL SELECT 'metric_info', COUNT(*) FROM metric_info "
                    "UNION ALL SELECT 'metric_variant', COUNT(*) FROM metric_variant "
                    "UNION ALL SELECT 'metric_variant_column', COUNT(*) FROM metric_variant_column "
                    "UNION ALL SELECT 'table_relation', COUNT(*) FROM table_relation"
                )
            )
        )
        table_metadata = _rows(
            await session.execute(
                text(
                    "SELECT id, name, role, grain FROM table_info ORDER BY id"
                )
            )
        )
        column_metadata = _rows(
            await session.execute(
                text(
                    "SELECT id, name, type, role, alias, table_id FROM column_info "
                    "ORDER BY table_id, id"
                )
            )
        )
        metric_metadata = _rows(
            await session.execute(
                text(
                    "SELECT m.id, m.name, m.alias, m.default_variant_id, "
                    "v.id AS variant_id, v.name AS variant_name, v.grain, "
                    "v.base_table, v.formula, v.default_filters, "
                    "v.suitable_dimensions, v.required_joins "
                    "FROM metric_info m JOIN metric_variant v ON v.metric_id = m.id "
                    "ORDER BY m.id, v.id"
                )
            )
        )
        metric_columns = _rows(
            await session.execute(
                text(
                    "SELECT variant_id, column_id FROM metric_variant_column "
                    "ORDER BY variant_id, column_id"
                )
            )
        )
        meta_relations = _rows(
            await session.execute(
                text(
                    "SELECT id, left_table, left_column, right_table, right_column, "
                    "relation_type FROM table_relation ORDER BY id"
                )
            )
        )

    dw = {
        "tables": tables,
        "columns": columns,
        "relations": relations,
        "row_counts": counts,
        "date_range": date_ranges,
        "safe_enumerations": safe_values,
        "metric_baseline_2025": metric_baseline,
        "item_baseline_2025_top5": item_baseline,
        "refund_baseline_2025": refund_baseline,
        "join_amplification_probe": join_risk,
        "explain_plan": explain,
    }
    meta = {
        "counts": meta_counts,
        "tables": table_metadata,
        "columns": column_metadata,
        "metrics_and_variants": metric_metadata,
        "metric_columns": metric_columns,
        "relations": meta_relations,
    }
    return dw, meta


async def audit_qdrant() -> dict[str, Any]:
    qdrant_client_manager.init()
    embedding_client_manager.init()
    client = qdrant_client_manager.client
    result: dict[str, Any] = {}
    for kind, collection_name in {
        "column": app_config.qdrant.column_collection_name,
        "metric": app_config.qdrant.metric_collection_name,
    }.items():
        info = await client.get_collection(collection_name)
        points, _ = await client.scroll(
            collection_name=collection_name,
            limit=5,
            with_payload=True,
            with_vectors=False,
        )
        result[kind] = {
            "collection": collection_name,
            "points_count": info.points_count,
            "vector_size": info.config.params.vectors.size,
            "payload_samples": [point.payload for point in points],
        }

    probes: dict[str, Any] = {}
    column_repo = ColumnQdrantRepository(client)
    metric_repo = MetricQdrantRepository(client)
    for question in ("2025年女装销售额", "按大区统计订单数"):
        embedding = await embedding_client_manager.client.aembed_query(question)
        columns = await column_repo.search(embedding, score_threshold=0.0, limit=5)
        metrics = await metric_repo.search(embedding, score_threshold=0.0, limit=5)
        probes[question] = {
            "columns": [
                {"id": item.id, "name": item.name, "table_id": item.table_id}
                for item in columns
            ],
            "metrics": [
                {"id": item.metric_id, "name": item.name} for item in metrics
            ],
        }
    result["top_k_probes"] = probes
    return result


async def audit_es() -> dict[str, Any]:
    es_client_manager.init()
    client = es_client_manager.client
    index_name = app_config.es.index_name
    mapping = await client.indices.get_mapping(index=index_name)
    count = await client.count(index=index_name)
    samples = await client.search(
        index=index_name,
        query={"match_all": {}},
        size=5,
        source_excludes=["value"],
    )
    repo = ValueESRepository(client)
    recall = await repo.search("女装", score_threshold=0.0, limit=20)
    return {
        "index": index_name,
        "document_count": count["count"],
        "mapping": mapping[index_name]["mappings"],
        "document_structure_samples": [
            hit["_source"] for hit in samples["hits"]["hits"]
        ],
        "women_clothing_recall": [
            {
                "value": item.value,
                "table_id": item.table_id,
                "column_id": item.column_id,
                "column_name": item.column_name,
            }
            for item in recall
        ],
        "multi_field_hit": len({item.column_id for item in recall}) > 1,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the UTF-8 JSON audit artifact.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    output: dict[str, Any] = {}
    try:
        output["dw_v2"], output["meta_v2"] = await audit_mysql()
        output["qdrant"] = await audit_qdrant()
        output["elasticsearch"] = await audit_es()
        rendered = json.dumps(output, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
    finally:
        if meta_mysql_client_manager.engine is not None:
            await meta_mysql_client_manager.close()
        if dw_mysql_client_manager.engine is not None:
            await dw_mysql_client_manager.close()
        if qdrant_client_manager.client is not None:
            await qdrant_client_manager.close()
        if es_client_manager.client is not None:
            await es_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
