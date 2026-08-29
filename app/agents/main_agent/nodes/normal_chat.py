"""Normal chat node for mainAgent."""

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agents.ask_agent.llm import llm
from app.agents.main_agent.context import MainAgentContext
from app.agents.main_agent.state import MainAgentState
from app.core.log import logger
from app.memory.session_memory import SessionMemory
from app.prompt.prompt_loader import load_prompt


def summarize_memory(memory: SessionMemory, max_messages: int = 6) -> str:
    """为普通聊天节点整理最近几轮会话摘要。"""

    recent_messages = memory.messages[-max_messages:]
    if not recent_messages:
        return "暂无历史会话。"
    role_names = {"user": "用户", "assistant": "助手"}
    return "\n".join(
        f"{role_names.get(message.role, message.role)}：{message.content}"
        for message in recent_messages
    )


async def normal_chat(state: MainAgentState, runtime: Runtime[MainAgentContext]):
    """Reply to ordinary chat and guide the user back to data queries."""

    writer = runtime.stream_writer
    logger.info("mainAgent 节点开始处理：normal_chat")

    try:
        prompt = PromptTemplate(
            template=load_prompt("main_agent/normal_chat"),
            input_variables=["message", "memory_summary"],
        )
        chain = prompt | llm | StrOutputParser()
        content_parts: list[str] = []
        async for chunk in chain.astream(
            {
                "message": state["message"],
                "memory_summary": summarize_memory(runtime.context["memory"]),
            }
        ):
            if not chunk:
                continue
            content_parts.append(chunk)
            writer({"type": "message_delta", "content": chunk})
        content = "".join(content_parts).strip()
        if not content:
            content = (
                "我可以帮你查询电商经营指标，并支持基于上一轮结果继续追问。"
            )
            writer({"type": "message", "role": "assistant", "content": content})
    except Exception as e:
        logger.warning(f"mainAgent 普通聊天调用 LLM 失败，已使用本地兜底回复：{e}")
        content = (
            "我可以帮你查询电商经营指标，并支持连续追问。"
            "你可以试着问：统计 2025 年第一季度各大区 GMV。"
        )
        writer({"type": "message", "role": "assistant", "content": content})

    logger.info(f"mainAgent 普通聊天模型回复：{content}")
    writer({"type": "final", "content": content})
    logger.info("mainAgent 节点处理完成：normal_chat")
    return {"final_response": content}
