"""工具调用适配层 · 数据规范（规则基座）

本模块零依赖：只定义常量与纯结构，**不 import 任何 provider**（时空防越界铁律）。
所有定界符、状态、提示词模板集中在此，保证 render 与 parse 共享同一套契约。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 定界符

TOOL_CALL_OPEN = "<tool_call>"
TOOL_CALL_CLOSE = "</tool_call>"
FENCE = "```"

# 流式解析保留的尾窗长度：保证跨 chunk 的定界符不被漏判
HOLDBACK = len(TOOL_CALL_OPEN) - 1


# ---------------------------------------------------------------- 状态与事件


class ParseState(Enum):
    NORMAL = "normal"
    IN_TOOL_CALL = "in_tool_call"
    IN_FENCE = "in_fence"


class EventKind(Enum):
    CONTENT = "content"
    TOOL_CALL = "tool_call"


@dataclass
class Event:
    kind: EventKind
    text: str = ""
    call: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------- 提示词模板

TOOL_RULES_HEAD = (
    "你是一个可以使用外部工具的助手。\n\n"
    "可用工具（JSON Schema）：\n"
    "<tools>\n"
)

TOOL_RULES_TAIL = (
    "\n</tools>\n\n"
    "调用规则：\n"
    "1. 需要工具时，只输出工具调用，不要输出任何其他文字：\n"
    "   <tool_call>{\"name\": \"工具名\", \"arguments\": {\"参数\": \"值\"}}</tool_call>\n"
    "2. 一轮需要多个工具时，连续输出多个 <tool_call>...</tool_call> 块。\n"
    "3. 不需要工具时，直接用自然语言回答，不要输出 <tool_call>。\n"
    "4. arguments 必须是合法 JSON 对象，且符合该工具的 parameters Schema。\n"
    "5. 只有当确实缺少必要信息时才调用工具；若 <conversation> 中已存在该工具的\n"
    "   <tool_result>，必须直接依据结果作答，不要重复调用同一工具。"
)

# 追加在 prompt 最末端的强提醒：模型对结尾内容的注意力权重最高（P1/P2 迭代结论）
OUTPUT_REMINDER = (
    "\n\n[输出要求] 若本轮需要调用工具，你的回复必须**只包含** <tool_call>...</tool_call>，"
    "不要包含任何解释、寒暄或前缀文字；若不需要工具，则直接给出答案。"
)

FORCE_ANY_HINT = "\n本轮你必须调用至少一个工具，不要直接回答。"
FORCE_ONE_HINT = "\n本轮你必须调用工具「{name}」，不要直接回答。"

EMPTY_PARAMETERS = {"type": "object", "properties": {}}


# ---------------------------------------------------------------- 规范化


def _get(obj: Any, key: str) -> Any:
    """同时兼容 pydantic 对象与 dict，避免调用方关心具体类型"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def normalize_tools(tools: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """把 OpenAI `tools` 规范化为 [{"name","description","parameters"}] 三元组"""
    normalized: List[Dict[str, Any]] = []
    for tool in tools or []:
        function = _get(tool, "function")
        if function is None:
            function = tool
        name = _get(function, "name")
        if not isinstance(name, str) or not name:
            continue
        parameters = _get(function, "parameters")
        normalized.append({
            "name": name,
            "description": _get(function, "description") or "",
            "parameters": parameters if isinstance(parameters, dict) else EMPTY_PARAMETERS,
        })
    return normalized


def forced_function_name(tool_choice: Any) -> Optional[str]:
    """从 tool_choice 中取出被强制的函数名（非强制时返回 None）"""
    if not isinstance(tool_choice, dict):
        return None
    name = _get(_get(tool_choice, "function"), "name")
    return name if isinstance(name, str) and name else None
