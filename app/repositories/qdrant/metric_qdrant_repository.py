"""
指标向量仓储

管理指标向量集合，并把 Service 层准备好的指标 point 批量写入 Qdrant

字段和指标虽然都用向量检索，但它们是两类不同对象
所以指标单独使用 metric_info_collection，避免后续召回时和字段结果混在一起
"""

import asyncio

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.conf.app_config import app_config
from app.core.log import logger
from app.entities.metric_recall_candidate import MetricRecallCandidate


class MetricQdrantRepository:
    """负责指标向量集合的创建 写入和基础检索"""

    collection_name = app_config.qdrant.metric_collection_name

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        """确保指标向量集合存在，并按当前 Embedding 维度初始化"""
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    # 向量维度必须和 Embedding 模型输出一致，否则写入时会失败
                    size=app_config.qdrant.embedding_size,
                    distance=Distance.COSINE,
                ),
            )

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        payloads: list[dict],
        batch_size: int = 10,
    ):
        """分批 upsert 指标向量点，避免一次提交过多 point"""
        # ids embeddings payloads 三个列表按相同下标组成一条完整的 Qdrant point
        points: list[PointStruct] = [
            PointStruct(id=id, vector=embedding, payload=payload)
            for id, embedding, payload in zip(ids, embeddings, payloads)
        ]
        for i in range(0, len(points), batch_size):
            await self.client.upsert(
                collection_name=self.collection_name, points=points[i : i + batch_size]
            )

    async def search(
        self, embedding: list[float], score_threshold: float = 0.6, limit: int = 20
    ) -> list[MetricRecallCandidate]:
        """按向量相似度检索轻量指标候选，完整口径由 Meta MySQL 补齐。"""

        max_attempts = 6
        for attempt in range(1, max_attempts + 1):
            try:
                result = await self._query_points(
                    self.client, embedding, score_threshold, limit
                )
                break
            except Exception as e:
                logger.warning(
                    f"Qdrant 指标向量检索失败，尝试使用临时客户端恢复：{e}"
                )
                try:
                    result = await self._query_points_with_fresh_client(
                        embedding, score_threshold, limit
                    )
                    break
                except Exception as fresh_error:
                    if attempt >= max_attempts:
                        raise fresh_error
                    logger.warning(
                        "Qdrant 指标向量检索临时客户端也失败，"
                        f"准备第 {attempt + 1} 次重试：{fresh_error}"
                    )
                await asyncio.sleep(min(2.0, 0.5 * attempt))

        return [MetricRecallCandidate(**point.payload) for point in result.points]

    async def _query_points(
        self,
        client: AsyncQdrantClient,
        embedding: list[float],
        score_threshold: float,
        limit: int,
    ):
        """使用指定 Qdrant 客户端执行指标向量检索。"""

        return await client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            limit=limit,
            score_threshold=score_threshold,
        )

    async def _query_points_with_fresh_client(
        self,
        embedding: list[float],
        score_threshold: float,
        limit: int,
    ):
        """共享客户端连接池异常时，临时创建新客户端兜底查询。"""

        url = f"http://{app_config.qdrant.host}:{app_config.qdrant.port}"
        client = AsyncQdrantClient(
            url=url,
            check_compatibility=False,
            trust_env=False,
        )
        try:
            return await self._query_points(
                client, embedding, score_threshold, limit
            )
        finally:
            await client.close()
