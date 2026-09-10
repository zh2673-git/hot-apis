"""工具调用适配层 · 逆向映射器（数据流转-核心规则）

把上游**不可信**的自然语言字符流还原成结构化 tool_calls。
本模块是 tools 包内唯一持有时间状态的地方（跨 chunk 解析状态机）。

格式支持（按优先级）：
    F1 `<tool_call>{json}</tool_call>`
    F2 ```json ... ``` 围栏
    F3 裸 JSON 对象（含 name + arguments/parameters）

降级铁律（R2/I2）：任何解析失败都**降级为正文**，绝不抛出、绝不 5xx。
"""

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ..models import FunctionCall, ToolCall
from .spec import (
    Event,
    EventKind,
    FENCE,
    HOLDBACK,
    ParseState,
    TOOL_CALL_CLOSE,
    TOOL_CALL_OPEN,
)

_ARGUMENT_KEYS = ("arguments", "parameters", "args")


def _safe_tail_length(buffer: str) -> int:
    """必须保留的尾部长度 = 最长的、且为某个定界符**前缀**的后缀

    纯文本时返回 0 → 立即产出（零缓冲延迟，断言 A-P4）；
    仅当尾部可能是一个尚未收齐的定界符时才扣留最少必要的字符。
    """
    limit = min(len(buffer), HOLDBACK)
    for length in range(limit, 0, -1):
        tail = buffer[-length:]
        if TOOL_CALL_OPEN.startswith(tail) or FENCE.startswith(tail):
            return length
    return 0


# ---------------------------------------------------------------- 基础解析


def _loads_object(text: str) -> Optional[Dict[str, Any]]:
    """宽松 JSON 对象解析：容忍前后夹杂文字（截取最外层花括号）"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _to_raw_call(obj: Any) -> Optional[Dict[str, Any]]:
    """规范化为 {"name", "arguments"}；不合法返回 None"""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if not isinstance(name, str) or not name:
        return None
    arguments = None
    for key in _ARGUMENT_KEYS:
        if key in obj:
            arguments = obj[key]
            break
    return {"name": name, "arguments": {} if arguments is None else arguments}


def _arguments_as_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)


def to_tool_calls(raw_calls: List[Dict[str, Any]]) -> List[ToolCall]:
    """补上 id/type，并把 arguments 封装为 JSON 字符串（OpenAI 规范）"""
    calls: List[ToolCall] = []
    for raw in raw_calls or []:
        calls.append(ToolCall(
            id=f"call_{uuid.uuid4().hex[:8]}",
            function=FunctionCall(name=raw["name"], arguments=_arguments_as_text(raw.get("arguments"))),
        ))
    return calls


# ---------------------------------------------------------------- 整包解析


def _extract_fenced(text: str) -> Tuple[List[Dict[str, Any]], str]:
    """从围栏代码块中提取工具调用（F2）；返回 (calls, 剩余文本)"""
    calls: List[Dict[str, Any]] = []
    pieces: List[str] = []
    rest = text
    while True:
        start = rest.find(FENCE)
        if start < 0:
            pieces.append(rest)
            break
        end = rest.find(FENCE, start + len(FENCE))
        if end < 0:
            pieces.append(rest)
            break
        inner = rest[start + len(FENCE):end]
        if inner.lstrip().lower().startswith("json"):
            inner = inner.lstrip()[4:]
        call = _to_raw_call(_loads_object(inner))
        if call:
            calls.append(call)
        else:
            pieces.append(rest[start:end + len(FENCE)])
        rest = rest[end + len(FENCE):]
    return calls, "".join(pieces)


def extract(text: str, enabled: bool = True) -> Tuple[str, List[Dict[str, Any]]]:
    """整包解析（非流式专用）：返回 (正文, raw_calls)"""
    if not enabled or not text:
        return (text or ""), []

    calls: List[Dict[str, Any]] = []
    pieces: List[str] = []
    rest = text
    while True:
        start = rest.find(TOOL_CALL_OPEN)
        if start < 0:
            pieces.append(rest)
            break
        end = rest.find(TOOL_CALL_CLOSE, start + len(TOOL_CALL_OPEN))
        pieces.append(rest[:start])
        if end < 0:
            pieces.append(rest[start:])
            break
        body = rest[start + len(TOOL_CALL_OPEN):end]
        call = _to_raw_call(_loads_object(body))
        if call:
            calls.append(call)
        else:
            pieces.append(rest[start:end + len(TOOL_CALL_CLOSE)])
        rest = rest[end + len(TOOL_CALL_CLOSE):]

    content = "".join(pieces)

    if not calls:
        calls, content = _extract_fenced(content)
    if not calls:
        call = _to_raw_call(_loads_object(content))
        if call:
            calls.append(call)
            content = ""

    return content.strip(), calls


# ---------------------------------------------------------------- 流式状态机


class ToolCallStreamParser:
    """跨 chunk 的解析状态机（唯一持有时间状态的地方）"""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._state = ParseState.NORMAL
        self._buffer = ""
        self._decided = False
        self._calls: List[Dict[str, Any]] = []

    # -- 只读观测

    @property
    def state(self) -> ParseState:
        return self._state

    def has_tool_calls(self) -> bool:
        return bool(self._calls)

    # -- 驱动

    def feed(self, chunk: str) -> List[Event]:
        if not self._enabled:
            return [Event(EventKind.CONTENT, text=chunk)] if chunk else []
        try:
            return self._feed(chunk or "")
        except Exception:
            # R2/I2：内部异常一律降级为正文
            pending, self._buffer = self._buffer, ""
            self._state = ParseState.NORMAL
            fallback = pending + (chunk or "")
            return [Event(EventKind.CONTENT, text=fallback)] if fallback else []

    def finish(self) -> List[Event]:
        events: List[Event] = []
        try:
            if self._state is ParseState.IN_TOOL_CALL:
                call = _to_raw_call(_loads_object(self._buffer))
                if call:
                    self._calls.append(call)
                    events.append(Event(EventKind.TOOL_CALL, call=call))
                elif self._buffer:
                    events.append(Event(EventKind.CONTENT,
                                        text=TOOL_CALL_OPEN + self._buffer))
                self._buffer = ""
            elif self._state is ParseState.IN_FENCE:
                inner = self._buffer
                if inner.lstrip().lower().startswith("json"):
                    inner = inner.lstrip()[4:]
                call = _to_raw_call(_loads_object(inner))
                if call:
                    self._calls.append(call)
                    events.append(Event(EventKind.TOOL_CALL, call=call))
                elif inner:
                    events.append(Event(EventKind.CONTENT, text=FENCE + inner))
                self._buffer = ""
        except Exception:
            if self._buffer:
                events.append(Event(EventKind.CONTENT, text=self._buffer))
            self._buffer = ""

        if self._buffer:
            events.append(Event(EventKind.CONTENT, text=self._buffer))
        self._buffer = ""
        self._state = ParseState.NORMAL
        return events

    # -- 内部

    def _enter(self, state: ParseState, marker: str, events: List[Event]) -> None:
        head = self._buffer[:self._buffer.find(marker)]
        self._buffer = self._buffer[len(head) + len(marker):]
        self._state = state
        self._decided = True
        # 纯空白前导（模型在标签前多吐的空格/换行）不作为正文输出
        if head and head.strip():
            events.append(Event(EventKind.CONTENT, text=head))

    def _emit_call(self, body: str, events: List[Event], wrap: str) -> None:
        call = _to_raw_call(_loads_object(body))
        if call:
            self._calls.append(call)
            events.append(Event(EventKind.TOOL_CALL, call=call))
        elif body:
            events.append(Event(EventKind.CONTENT, text=wrap.format(body=body)))

    def _feed(self, chunk: str) -> List[Event]:
        events: List[Event] = []
        self._buffer += chunk

        while True:
            if self._state is ParseState.IN_TOOL_CALL:
                index = self._buffer.find(TOOL_CALL_CLOSE)
                if index < 0:
                    break
                body, self._buffer = self._buffer[:index], self._buffer[index + len(TOOL_CALL_CLOSE):]
                self._state = ParseState.NORMAL
                self._decided = True
                self._emit_call(body, events, TOOL_CALL_OPEN + "{body}" + TOOL_CALL_CLOSE)
                continue

            if self._state is ParseState.IN_FENCE:
                index = self._buffer.find(FENCE)
                if index < 0:
                    break
                body, self._buffer = self._buffer[:index], self._buffer[index + len(FENCE):]
                self._state = ParseState.NORMAL
                self._decided = True
                if body.lstrip().lower().startswith("json"):
                    body = body.lstrip()[4:]
                self._emit_call(body, events, FENCE + "{body}" + FENCE)
                continue

            # ---- NORMAL
            if not self._buffer:
                break

            if not self._decided:
                stripped = self._buffer.lstrip()
                if not stripped:
                    break
                if TOOL_CALL_OPEN.startswith(stripped) or FENCE.startswith(stripped):
                    break                       # 仍可能落在定界符上，继续等
                if stripped.startswith(TOOL_CALL_OPEN):
                    self._enter(ParseState.IN_TOOL_CALL, TOOL_CALL_OPEN, events)
                    continue
                if stripped.startswith(FENCE):
                    self._enter(ParseState.IN_FENCE, FENCE, events)
                    continue
                self._decided = True            # 判定为普通文本 → 走直通 + 尾窗

            index = self._buffer.find(TOOL_CALL_OPEN)
            if index >= 0:
                head, self._buffer = self._buffer[:index], self._buffer[index + len(TOOL_CALL_OPEN):]
                self._state = ParseState.IN_TOOL_CALL
                if head and head.strip():
                    events.append(Event(EventKind.CONTENT, text=head))
                continue

            keep = _safe_tail_length(self._buffer)
            if keep:
                emit = self._buffer[:len(self._buffer) - keep]
                self._buffer = self._buffer[-keep:]
            else:
                emit, self._buffer = self._buffer, ""
            if emit:
                events.append(Event(EventKind.CONTENT, text=emit))
            break

        return events
