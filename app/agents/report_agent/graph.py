"""reportAgent graph placeholder."""

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agents.report_agent.nodes.generate_stub import generate_stub
from app.agents.report_agent.state import ReportAgentState


graph_builder = StateGraph(state_schema=ReportAgentState, context_schema=dict)
graph_builder.add_node("generate_stub", generate_stub)
graph_builder.add_edge(START, "generate_stub")
graph_builder.add_edge("generate_stub", END)

graph = graph_builder.compile()
