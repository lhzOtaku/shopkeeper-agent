"""
电商问数 Agent 图编排

使用 LangGraph 把问数智能体的各个节点串成一条可观测的执行链路
当前链路已经落地关键词抽取和多路召回，字段和指标走 Qdrant 向量检索，字段取值走 ES 全文检索
整体流程先抽取用户问题关键词，再并行召回字段 字段取值和指标信息，
随后合并召回结果 过滤候选表和指标，为每个指标选择最优计算口径，
补充生成上下文和额外信息，最后生成 校验 循环修正并执行 SQL

┌─────────────────────────────────────────────────────────────────────┐
│                        Data Query Agent Graph                       │
│                         (15 Nodes · 指标口径 · 修正循环)             │
└─────────────────────────────────────────────────────────────────────┘

                                    START
                                      │
                                      ▼
                            ┌──────────────────┐
                            │  extract_keywords │  ← Jieba TF-IDF
                            └────────┬─────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                  │
                    ▼                 ▼                  ▼
            ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
            │ recall_column│ │ recall_value │ │recall_metric │
            │  (Qdrant)    │ │    (ES)      │ │  (Qdrant)    │
            └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
                    │                 │                  │
                    └─────────────────┼──────────────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │merge_retrieved_  │  ← 合并去重 + 补齐
                            │    info          │     主外键 + 指标字段
                            └────────┬─────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                  │
                    ▼                 │                  ▼
            ┌──────────────┐         │          ┌──────────────┐
            │ filter_table │         │          │filter_metric │
            │   (LLM)      │         │          │   (LLM)      │
            └──────┬───────┘         │          └──────┬───────┘
                    │                 │                  │
                    └─────────────────┼──────────────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │ select_metric_   │  ← 按上下文选择
                            │   variant        │    最佳指标口径
                            └────────┬─────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │ enrich_          │  ← 补充表关系/
                            │ generation_      │    粒度信息/Join路径
                            │   context        │
                            └────────┬─────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │ add_extra_       │  ← 注入日期/DB
                            │   context        │     方言/版本
                            └────────┬─────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │   generate_sql   │  ← LLM 生成 SQL
                            └────────┬─────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │  validate_sql    │  ← EXPLAIN 验证
                            └────────┬─────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                  │
               error=None       error!=null       sql_safety_
                               correction       blocked 或
                               count < MAX     count ≥ MAX
                    │                 │                  │
                    ▼                 ▼                  ▼
              ┌──────────┐   ┌──────────────┐  ┌──────────────────┐
              │  run_sql │   │ correct_sql  │  │ fail_sql_        │
              │         │   │  (LLM 修正)   │  │   correction     │  ← 抛出异常终止
              └────┬─────┘   └──────┬───────┘  └────────┬─────────┘
                    │                │                    │
                    │                └── 返回 ────────────┘
                    │                validate_sql ⋯
                    │                (最多 MAX=3 次)
                    ▼                   ▼                   ▼
                   END                 END                 END
"""

import asyncio

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.nodes.add_extra_context import add_extra_context
from app.agents.ask_agent.nodes.correct_sql import correct_sql
from app.agents.ask_agent.nodes.enrich_generation_context import (
    enrich_generation_context,
)
from app.agents.ask_agent.nodes.extract_keywords import extract_keywords
from app.agents.ask_agent.nodes.fail_sql_correction import fail_sql_correction
from app.agents.ask_agent.nodes.filter_metric import filter_metric
from app.agents.ask_agent.nodes.filter_table import filter_table
from app.agents.ask_agent.nodes.generate_sql import generate_sql
from app.agents.ask_agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agents.ask_agent.nodes.recall_column import recall_column
from app.agents.ask_agent.nodes.recall_metric import recall_metric
from app.agents.ask_agent.nodes.recall_value import recall_value
from app.agents.ask_agent.nodes.run_sql import run_sql
from app.agents.ask_agent.nodes.select_metric_variant import select_metric_variant
from app.agents.ask_agent.nodes.validate_sql import validate_sql
from app.agents.ask_agent.state import DataAgentState
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

MAX_SQL_CORRECTION_ATTEMPTS = 3


def route_after_sql_validation(state: DataAgentState) -> str:
    """根据 SQL 校验结果决定执行、校正或失败退出。"""

    if state.get("sql_safety_blocked"):
        return "fail_sql_correction"
    if state.get("error") is None:
        return "run_sql"
    if state.get("correction_count", 0) < MAX_SQL_CORRECTION_ATTEMPTS:
        return "correct_sql"
    return "fail_sql_correction"

# StateGraph 声明整张图使用的状态结构和运行时上下文结构
graph_builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

# 注册节点：每个节点负责问数链路中的一个清晰步骤
graph_builder.add_node("extract_keywords", extract_keywords)
graph_builder.add_node("recall_column", recall_column)
graph_builder.add_node("recall_value", recall_value)
graph_builder.add_node("recall_metric", recall_metric)
graph_builder.add_node("merge_retrieved_info", merge_retrieved_info)
graph_builder.add_node("filter_metric", filter_metric)
graph_builder.add_node("filter_table", filter_table)
graph_builder.add_node("select_metric_variant", select_metric_variant)
graph_builder.add_node("enrich_generation_context", enrich_generation_context)
graph_builder.add_node("add_extra_context", add_extra_context)
graph_builder.add_node("generate_sql", generate_sql)
graph_builder.add_node("validate_sql", validate_sql)
graph_builder.add_node("correct_sql", correct_sql)
graph_builder.add_node("fail_sql_correction", fail_sql_correction)
graph_builder.add_node("run_sql", run_sql)

# 从用户问题开始，先抽取关键词作为后续检索的基础
graph_builder.add_edge(START, "extract_keywords")

# 关键词抽取后并行进入三类召回，分别面向字段 字段值和业务指标
graph_builder.add_edge("extract_keywords", "recall_column")
graph_builder.add_edge("extract_keywords", "recall_value")
graph_builder.add_edge("extract_keywords", "recall_metric")

# 三路召回都完成后，再进入统一的信息合并节点
graph_builder.add_edge("recall_column", "merge_retrieved_info")
graph_builder.add_edge("recall_value", "merge_retrieved_info")
graph_builder.add_edge("recall_metric", "merge_retrieved_info")

# 合并后的候选信息继续拆成表过滤和指标过滤两条线
graph_builder.add_edge("merge_retrieved_info", "filter_table")
graph_builder.add_edge("merge_retrieved_info", "filter_metric")

# 表和指标都过滤完成后，先为每个指标选择最终粒度口径
graph_builder.add_edge("filter_table", "select_metric_variant")
graph_builder.add_edge("filter_metric", "select_metric_variant")
graph_builder.add_edge("select_metric_variant", "enrich_generation_context")
graph_builder.add_edge("enrich_generation_context", "add_extra_context")
graph_builder.add_edge("add_extra_context", "generate_sql")
graph_builder.add_edge("generate_sql", "validate_sql")

# SQL 校验通过才执行；校验失败则进入修正节点，修正后必须重新校验
graph_builder.add_conditional_edges(
    source="validate_sql",
    path=route_after_sql_validation,
    path_map={
        "run_sql": "run_sql",
        "correct_sql": "correct_sql",
        "fail_sql_correction": "fail_sql_correction",
    },
)
graph_builder.add_edge("correct_sql", "validate_sql")
graph_builder.add_edge("run_sql", END)
graph_builder.add_edge("fail_sql_correction", END)

# 编译后的 graph 是对外使用的 Agent 执行入口
graph = graph_builder.compile()

# print(graph.get_graph().draw_mermaid())

if __name__ == "__main__":

    async def test():
        """本地调试关键词抽取和字段 指标 取值三路召回链路"""

        # 多路召回和上下文补全会访问 Qdrant、Embedding、ES、Meta MySQL 和 DW MySQL
        qdrant_client_manager.init()
        embedding_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()

        # Meta MySQL 用来补齐元数据，DW MySQL 用来读取数据库方言和版本
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            meta_mysql_repository = MetaMySQLRepository(meta_session)
            dw_mysql_repository = DWMySQLRepository(dw_session)

            # 字段和指标分别使用不同 Qdrant collection，取值检索使用 ES index
            column_qdrant_repository = ColumnQdrantRepository(
                qdrant_client_manager.client
            )
            metric_qdrant_repository = MetricQdrantRepository(
                qdrant_client_manager.client
            )
            value_es_repository = ValueESRepository(es_client_manager.client)

            # 当前只需要传入原始问题，后续节点会逐步写回召回、过滤和额外上下文结果
            state = DataAgentState(query="统计华北地区的销售总额")
            context = DataAgentContext(
                column_qdrant_repository=column_qdrant_repository,
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=metric_qdrant_repository,
                value_es_repository=value_es_repository,
                meta_mysql_repository=meta_mysql_repository,
                dw_mysql_repository=dw_mysql_repository,
            )

            # stream_mode="custom" 会接收各节点通过 runtime.stream_writer 写出的进度信息
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                print(chunk)

        # 关闭显式创建的异步客户端，避免本地调试时连接资源悬挂
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()




    asyncio.run(test())
