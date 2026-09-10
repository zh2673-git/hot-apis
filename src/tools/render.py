"""工具调用适配层 · 正向映射器（数据流转-核心规则）

职责：把结构化规则（工具规范 + 角色化对话历史）**降维**成一条自然语言文本。
为什么必须降维：上游 kimi / doubao / qwen 只取最后一条 user 消息（已实测确认），
结构化信息只能编码进一条文本才能穿过这道窄门。

本模块无副作用、无状态。
"""

import json
from typing import Any, List, Optional

from ..models import ChatMessage
from .spec import (
    FORCE_ANY_HINT,
    FORCE_ONE_HINT,
    OUTPUT_REMINDER,
    TOOL_RULES_HEAD,
    TOOL_RULES_TAIL,
    forced_function_name,
)


def build_system_prompt(tools: List[dict], tool_choice: Any = None) -> str:
    """工具规范 + 调用规则 → 系统提示文本；无工具或 tool_choice=none 时返回空串"""
    if not tools or tool_choice == "none":
        return ""

    body = "\n".join(json.dumps(tool, ensure_ascii=False) for tool in tools)
    prompt = TOOL_RULES_HEAD + body + TOOL_RULES_TAIL

    if tool_choice == "required":
        prompt += FORCE_ANY_HINT
    else:
        name = forced_function_name(tool_choice)
        if name:
            prompt += FORCE_ONE_HINT.format(name=name)
    return prompt


def _render_block(message: ChatMessage) -> Optional[str]:
    content = message.content or ""
    if message.role == "assistant":
        parts = []
        for call in message.tool_calls or []:
            parts.append(
                f'<assistant_call name="{call.function.name}" call_id="{call.id}">'
                f"{call.function.arguments}</assistant_call>"
            )
        if content:
            parts.append(f"<assistant>\n{content}\n</assistant>")
        return "\n".join(parts) or None
    if message.role == "user":
        return f"<user>\n{content}\n</user>"
    if message.role == "tool":
        return f'<tool_result call_id="{message.tool_call_id or ""}">\n{content}\n</tool_result>'
    return None


def render_messages(
    messages: List[ChatMessage],
    tools: List[dict],
    tool_choice: Any = None,
) -> List[ChatMessage]:
    """压平完整历史为**长度恒为 1** 的 user 消息（断言 A-R1）

    - `content is None` 一律归一化为 `""`（断言 A-R2，不得让 None 漏到 provider）
    - 最后一条 user 消息单独放入 `<current_user_message>`，不与历史重复
    """
    last_user_idx = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user" and (messages[index].content or ""):
            last_user_idx = index
            break

    system_parts: List[str] = []
    blocks: List[str] = []
    for index, message in enumerate(messages):
        if message.role == "system":
            system_parts.append(message.content or "")
        elif index == last_user_idx:
            continue
        else:
            block = _render_block(message)
            if block:
                blocks.append(block)

    sections: List[str] = []

    system_prompt = build_system_prompt(tools, tool_choice)
    head_parts = [part for part in system_parts if part]
    if system_prompt:
        head_parts.append(system_prompt)
    if head_parts:
        sections.append("\n\n".join(head_parts))

    if blocks:
        sections.append("<conversation>\n" + "\n".join(blocks) + "\n</conversation>")

    if last_user_idx is not None:
        current_user_text = messages[last_user_idx].content or ""
    elif messages:
        current_user_text = messages[-1].content or ""
    else:
        current_user_text = ""
    sections.append("<current_user_message>\n" + current_user_text + "\n</current_user_message>")

    if system_prompt:
        # 结尾强提醒：模型对 prompt 末端的注意力权重最高
        sections.append(OUTPUT_REMINDER.strip())

    return [ChatMessage(role="user", content="\n\n".join(sections))]
