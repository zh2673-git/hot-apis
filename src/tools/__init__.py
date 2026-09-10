"""工具调用适配层（方案 A：提示词模拟）

本质：一个**无状态的协议翻译层** —— 把「只能收自然语言、只能回自然语言」的聊天模型，
包装成「能收 tools 规范、能回 tool_calls」的 OpenAI 兼容端点。

时空契约：
- 空间：工具调用多轮状态**不驻留 relay**；请求级对象随请求作用域回收
- 时间：顺序管道 + 局部状态机（仅存在于 parse.ToolCallStreamParser）
- 规则：运行时拦截 + 失败降级（解析失败一律降级为正文，绝不 5xx）

生命周期钩子：**四个均不适用**（纯函数集合，无资源持有）。
详见 docs/04-tools-四层设计.md §1.3。
"""

from .parse import ToolCallStreamParser, extract, to_tool_calls
from .pipeline import Prepared, build_response, prepare, stream
from .render import build_system_prompt, render_messages
from .spec import Event, EventKind, ParseState, normalize_tools

__all__ = [
    "Event",
    "EventKind",
    "ParseState",
    "Prepared",
    "ToolCallStreamParser",
    "build_response",
    "build_system_prompt",
    "extract",
    "normalize_tools",
    "prepare",
    "render_messages",
    "stream",
    "to_tool_calls",
]
