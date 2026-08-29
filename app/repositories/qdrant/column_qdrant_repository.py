"""
字段向量仓储

管理字段向量集合并把已经准备好的 point 批量写入 Qdrant

Service 层负责决定一个字段要拆成哪些 point
Repository 只关心集合存在和向量点如何稳定落库
"""

import asyncio

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import PointStruct
from qdrant_client.models import Distance, VectorParams

from app.conf.app_config import app_config
from app.core.log import logger
from app.entities.column_info import ColumnInfo


class ColumnQdrantRepository:
    """负责字段向量集合的创建 写入和基础检索"""

    collection_name = app_config.qdrant.column_collection_name

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        """确保字段向量集合存在，并按配置中的维度初始化"""
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=app_config.qdrant.embedding_size, distance=Distance.COSINE
                ),
            )

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        payloads: list[dict],
        batch_size: int = 10,
    ):
        """分批 upsert 字段向量点，避免一次提交过多 point"""
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
    ) -> list[ColumnInfo]:
        """按向量相似度检索字段元数据，并还原为 ColumnInfo 实体"""

        max_attempts = 6
        for attempt in range(1, max_attempts + 1):
            try:
                result = await self._query_points(
                    self.client, embedding, score_threshold, limit
                )
                break
            except Exception as e:
                logger.warning(
                    f"Qdrant 字段向量检索失败，尝试使用临时客户端恢复：{e}"
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
                        "Qdrant 字段向量检索临时客户端也失败，"
                        f"准备第 {attempt + 1} 次重试：{fresh_error}"
                    )
                await asyncio.sleep(min(2.0, 0.5 * attempt))

        # Qdrant 只保存字段元数据 payload，业务层继续使用 ColumnInfo
        return [ColumnInfo(**point.payload) for point in result.points]

    async def _query_points(
        self,
        client: AsyncQdrantClient,
        embedding: list[float],
        score_threshold: float,
        limit: int,
    ):
        """使用指定 Qdrant 客户端执行字段向量检索。"""

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
