"""Adapter that exposes the existing data-query graph as askAgent.

┌─────────────────────────────────────────────────────────────────────┐
│                       askAgent (Adapter 层)                         │
│  接收 mainAgent 请求 → 驱动 data_query_graph → 事件标准化后返回     │
└─────────────────────────────────────────────────────────────────────┘

                             mainAgent
                                │
                    call_ask_agent(query)
                                │
                                ▼
                      ┌──────────────────┐
                      │  AskAgentAdapter  │
                      │   .stream(query)  │
                      └────────┬─────────┘
                               │
                   ┌───────────┴───────────┐
                   │    data_query_graph    │
                   │   (app/agent/graph)   │
                   └───────────┬───────────┘
                               │  astream()
                               ▼
                      ┌──────────────────┐
                      │  事件标准化转换    │
                      │                  │
                      │ progress ─→ tool_progress  │
                      │ sql      ─→ tool_sql       │
                      │ result   ─→ → 缓存到末尾   │
                      │ query_spec → → 缓存到末尾  │
                      │ error    ─→ error          │
                      └────────┬───────────────────┘
                               │
                               ▼
                      ┌──────────────────┐
                      │   tool_result     │
                      │  (query + sql +   │
                      │   rows + spec)    │
                      └──────────────────┘
                               │
                        mainAgent 继续

┌─────────────────────────────────────────────────────────────────────┐
│  底层：data_query_graph (14 节点 · app/agents/ask_agent/graph.py)  │
└─────────────────────────────────────────────────────────────────────┘

  START → extract_keywords
            │
        ┌───┼───┐
        ▼   ▼   ▼
    recall  recall  recall
    column  value   metric
    (Qdrant) (ES)  (Qdrant)
        │   │   │
        └───┼───┘
            ▼
    merge_retrieved_info
            │
        ┌───┴───┐
        ▼       ▼
    filter  filter
    table   metric
    (LLM)   (LLM)
        │   │
        └───┘
            ▼
    select_metric_variant
            ▼
    enrich_generation_context
            ▼
    generate_sql (LLM)
            ▼
    validate_sql (EXPLAIN)
            │
    ┌───────┼───────┐
    │       │       │
error=None error   error
          <3次    ≥3次
    │       │       │
    ▼       ▼       ▼
  run_sql correct  fail_sql
          sql      correction
    │     │         │
    │     └──┐      │
    ▼        ▼      ▼
   END      END    END
"""

from collections.abc import AsyncIterator
from typing import Any

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.graph import graph as data_query_graph
from app.agents.ask_agent.state import DataAgentState
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


class AskAgentAdapter:
    """Run the original ask graph and normalize its stream for mainAgent."""

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

    async def stream(self, query: str) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized askAgent events while the data-query graph runs."""

        state = DataAgentState(query=query)
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
        )

        sql: str | None = None
        rows: list[dict[str, Any]] | None = None
        query_spec: dict[str, Any] | None = None

        async for chunk in data_query_graph.astream(
            input=state, context=context, stream_mode="custom"
        ):
            if chunk.get("type") == "progress":
                yield {
                    "type": "tool_progress",
                    "tool": "askAgent",
                    "step": chunk.get("step", ""),
                    "status": chunk.get("status", "running"),
                }
            elif chunk.get("type") == "sql":
                sql = chunk.get("sql")
                yield {"type": "tool_sql", "tool": "askAgent", "sql": sql}
            elif chunk.get("type") == "result":
                rows = chunk.get("data")
            elif chunk.get("type") == "query_spec":
                query_spec = chunk.get("data")
            elif chunk.get("type") == "error":
                yield {
                    "type": "error",
                    "message": chunk.get("message", "askAgent 执行失败"),
                }

        yield {
            "type": "tool_result",
            "tool": "askAgent",
            "data": {
                "success": bool(query_spec),
                "query": query,
                "sql": sql,
                "rows": rows or [],
                "query_spec": query_spec,
                "error_type": None if query_spec else "unknown",
                "error_message": None
                if query_spec
                else "askAgent 未返回成功查询上下文。",
                "retryable": False,
            },
        }
