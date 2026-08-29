"""Chat service that drives mainAgent."""

import json

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.core.log import logger
from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.graph import graph as main_agent_graph
from app.agents.main_agent.state import MainAgentState
from app.memory.memory_store import memory_store
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


def normalize_error_message(error: Exception) -> str:
    """Convert low-level dependency errors into user-facing Chinese messages."""

    detail = str(error)
    connection_markers = (
        "Server disconnected without sending a response",
        "All connection attempts failed",
        "Connection error",
        "ConnectError",
        "RemoteProtocolError",
    )
    if any(marker in detail for marker in connection_markers):
        return (
            "askAgent 依赖的外部服务暂时不可用。"
            "请确认 Embedding、Qdrant、MySQL、Elasticsearch 等服务已经启动，"
            "并等待 Embedding 模型完成 Ready 后再重试。"
        )
    return f"mainAgent 执行失败：{detail}"


class ChatService:
    """Run one mainAgent chat turn and stream normalized events."""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
    ):
        self.meta_mysql_repository = meta_mysql_repository
        self.embedding_client = embedding_client
        self.dw_mysql_repository = dw_mysql_repository
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository

    async def chat(self, session_id: str, message: str):
        """Handle one user message with mainAgent and stream SSE text."""

        memory = memory_store.get_or_create(session_id)
        state = MainAgentState(session_id=session_id, message=message)
        context = MainAgentContext(
            memory=memory,
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
        )

        try:
            async for chunk in main_agent_graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            logger.exception(f"mainAgent 执行失败：{e}")
            error = {"type": "error", "message": normalize_error_message(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
