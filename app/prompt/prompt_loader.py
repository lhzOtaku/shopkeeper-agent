"""
Prompt 模板加载工具

按名称从项目根目录的 prompts 目录读取 .prompt 文件
业务节点只需要传入逻辑名称，不需要关心提示词文件的具体路径
支持子目录路径，例如 load_prompt("main_agent/classify_intent")。
"""

from pathlib import Path


def load_prompt(name: str) -> str:
    """读取指定名称的 prompt 模板内容"""

    # app/prompt/prompt_loader.py 向上两级回到项目根目录，再进入 prompts 目录
    prompt_root = Path(__file__).parents[2] / "prompts"
    relative_path = Path(f"{name}.prompt")
    prompt_path = (prompt_root / relative_path).resolve()

    if prompt_root.resolve() not in prompt_path.parents:
        raise ValueError(f"Invalid prompt path: {name}")

    return prompt_path.read_text(encoding="utf-8")
