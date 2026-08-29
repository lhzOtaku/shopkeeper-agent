"""Embedding retry helpers for askAgent retrieval nodes."""

import asyncio

import httpx
from huggingface_hub.utils import set_async_client_factory
from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.conf.app_config import app_config
from app.core.log import logger


def build_embedding_client() -> HuggingFaceEndpointEmbeddings:
    """Create a fresh embedding client for recovery after a broken connection."""

    set_async_client_factory(lambda: httpx.AsyncClient(trust_env=False))
    url = f"http://{app_config.embedding.host}:{app_config.embedding.port}"
    return HuggingFaceEndpointEmbeddings(model=url)


async def embed_query_with_retry(
    embedding_client: HuggingFaceEndpointEmbeddings,
    text: str,
    max_attempts: int = 6,
) -> list[float]:
    """Embed one query with retry and a fresh-client fallback."""

    for attempt in range(1, max_attempts + 1):
        try:
            return await embedding_client.aembed_query(text)
        except Exception as e:
            logger.warning(
                f"Embedding 查询失败，尝试使用临时客户端恢复：{e}"
            )
            try:
                return await build_embedding_client().aembed_query(text)
            except Exception as fresh_error:
                if attempt >= max_attempts:
                    raise fresh_error
                logger.warning(
                    "Embedding 查询临时客户端也失败，"
                    f"准备第 {attempt + 1} 次重试：{fresh_error}"
                )
                await asyncio.sleep(min(2.0, 0.5 * attempt))

    raise RuntimeError("Embedding 查询失败")
