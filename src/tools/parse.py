"""工具调用适配层 · 逆向映射器（数据流转-核心规则）

把上游**不可信**的自然语言字符流还原成结构化 tool_calls。
本模块是 tools 包内唯一持有时间状态的地方（跨 chunk 解析状态机）。

格式支持（按优先级）：
    F1 `<tool_call>{json}</tool_call>`
    F2 ```json ... ``` 围栏
    F3 裸 JSON 对象（含 name + arguments/parameters）
    F4 XML 形态：`<tool_call><invoke name=".."><parameter name=".." string="true">值</parameter></invoke></tool_call>`
       —— 模型在工具集较大时会习惯性退回这种自带类型标注的 XML（2026-09-11 用 13 工具链真机复现）
    F5 DSML 标记：`<[字面量]||DSML|| calls>` / `<... invoke name="..">` / `<... parameter name=".." string="true">值</... parameter>`
       —— 上游内置工具协议的原始标记形态（元素名与属性同 F4，区别只在标签里夹了 DSML 标记）；
          先经 `_normalize_dsml` 归一成标准标签，再交给 F4 解析

降级铁律（R2/I2）：任何解析失败都**降级为正文**，绝不抛出、绝不 5xx。
"""

import json
import re
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


# ---- F4：XML 形态（<invoke> / <parameter>） ------------------------------------

_XML_INVOKE_RE = re.compile(r"<invoke\s+([^>]*?)>(.*?)</invoke>", re.S)
_XML_PARAM_RE = re.compile(r"<parameter\s+([^>]*?)>(.*?)</parameter>", re.S)
_XML_NAME_ATTR_RE = re.compile(r'name\s*=\s*"([^"]*)"')
_XML_STRING_ATTR_RE = re.compile(r'string\s*=\s*"(true|false)"')


def _coerce_value(text: str, string_attr: Optional[str]) -> Any:
    """`string="false"` = 模型已声明该参数不是字符串 → 还原成 JSON 标量（5 → 5，true → True）"""
    if string_attr == "false":
        try:
            return json.loads(text)
        except Exception:
            pass
    return text


def _calls_from_xml(body: str) -> List[Dict[str, Any]]:
    """从 <invoke>/<parameter> 中提取调用（一个块可含多个 invoke）"""
    calls: List[Dict[str, Any]] = []
    for attrs, inner in _XML_INVOKE_RE.findall(body or ""):
        name_match = _XML_NAME_ATTR_RE.search(attrs)
        if not name_match or not name_match.group(1):
            continue
        arguments: Dict[str, Any] = {}
        for param_attrs, value in _XML_PARAM_RE.findall(inner):
            key_match = _XML_NAME_ATTR_RE.search(param_attrs)
            if not key_match or not key_match.group(1):
                continue
            string_match = _XML_STRING_ATTR_RE.search(param_attrs)
            arguments[key_match.group(1)] = _coerce_value(
                value.strip(), string_match.group(1) if string_match else None
            )
        calls.append({"name": name_match.group(1), "arguments": arguments})
    return calls


def _calls_from_block(body: str) -> List[Dict[str, Any]]:
    """一个候选块 → 调用列表：先 JSON（F1–F3），再 XML（F4）"""
    call = _to_raw_call(_loads_object(body))
    if call:
        return [call]
    return _calls_from_xml(body)


# ---- F5：DSML 标记归一化 --------------------------------------------------------

# 上游内置工具协议的标签形如 `<tool||DSML|| calls>` / `<||DSML|| invoke name="..">`：
# 竖线为全角（U+FF5C）或 ASCII，「字面量 + 标记」两种前缀都可能出现。归一化后
# 就是标准的 `<tool_call>` / `<invoke>` / `<parameter>`，直接复用 F4。
_BAR_CLASS = "|｜"
_DSML_TOKEN = r"[|｜]{2,}[ \t]*DSML[ \t]*[|｜]{2,}"
_DSML_OPEN_RE = re.compile(r"<([A-Za-z_]*)" + _DSML_TOKEN + r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)")
_DSML_CLOSE_RE = re.compile(r"</([A-Za-z_]*)" + _DSML_TOKEN + r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)")
# 外壳元素名 → 归一为既有块标签，复用 F1 的块扫描
_DSML_WRAPPER_NAMES = {"calls", "tool_calls", "toolcall", "tool_call"}
# 可能的「半个标签」：尖括号 + 标签合法字符（字面量/斜线/空白/竖线），且还没有右尖括号
_PARTIAL_TAIL_RE = re.compile(r"</?[A-Za-z_0-9 \t/|｜]*")


def _dsml_element_name(name: str) -> str:
    return "tool_call" if name in _DSML_WRAPPER_NAMES else name


def _normalize_dsml(text: str) -> str:
    """把 DSML 标记标签归一成标准 XML 标签（保持属性原样）"""
    if not text or "DSML" not in text:
        return text

    def _open(match: "re.Match") -> str:
        return "<" + _dsml_element_name(match.group(2))

    def _close(match: "re.Match") -> str:
        return "</" + _dsml_element_name(match.group(2))

    return _DSML_CLOSE_RE.sub(_close, _DSML_OPEN_RE.sub(_open, text))


def _is_partial_dsml(tail: str) -> bool:
    """尾巴是否可能是「半个 DSML 标签」

    上游会把标记切成任意碎片（实测出现过只来一个 `<`、或只来 `</` 与 `||DSML||` 分段）。
    只要尾巴是「尖括号开头、尚未收到右尖括号、且只由标签合法字符组成」，就必须扣留——
    否则 `</` 会先被当正文发出，随后不含尖括号的 `||DSML||` 再也无法被识别。
    """
    if not tail or ">" in tail:
        return False
    return bool(_PARTIAL_TAIL_RE.fullmatch(tail))


class _DsmlNormalizer:
    """流式归一化器：把 DSML 标签转成标准标签后再交给状态机

    必须扣留「可能是半个标记」的尾巴，否则字面量前缀（如 `<tool`）会先被当正文吐出去，
    标记收齐后又重复输出。
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        self._buffer += chunk or ""
        if "DSML" in self._buffer or "<" in self._buffer:
            index = self._buffer.rfind("<")
            if index >= 0 and _is_partial_dsml(self._buffer[index:]):
                head, self._buffer = self._buffer[:index], self._buffer[index:]
                return _normalize_dsml(head)
        text, self._buffer = self._buffer, ""
        return _normalize_dsml(text)

    def flush(self) -> str:
        text, self._buffer = self._buffer, ""
        return _normalize_dsml(text)


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
        found = _calls_from_block(inner)
        if found:
            calls.extend(found)
        else:
            pieces.append(rest[start:end + len(FENCE)])
        rest = rest[end + len(FENCE):]
    return calls, "".join(pieces)


def extract(text: str, enabled: bool = True) -> Tuple[str, List[Dict[str, Any]]]:
    """整包解析（非流式专用）：返回 (正文, raw_calls)"""
    if not enabled or not text:
        return (text or ""), []

    text = _normalize_dsml(text)
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
        found = _calls_from_block(body)
        if found:
            calls.extend(found)
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
    if not calls:
        # F4 兜底：模型没包 <tool_call> 外壳，直接吐了 <invoke>
        found = _calls_from_xml(content)
        if found:
            calls.extend(found)
            content = _XML_INVOKE_RE.sub("", content)
            content = content.replace(TOOL_CALL_OPEN, "").replace(TOOL_CALL_CLOSE, "")

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
        self._dsml = _DsmlNormalizer()

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
            return self._feed(self._dsml.feed(chunk or ""))
        except Exception:
            # R2/I2：内部异常一律降级为正文
            pending, self._buffer = self._buffer, ""
            self._state = ParseState.NORMAL
            fallback = pending + (chunk or "")
            return [Event(EventKind.CONTENT, text=fallback)] if fallback else []

    def finish(self) -> List[Event]:
        events: List[Event] = []
        try:
            if self._enabled:
                self._buffer += self._dsml.flush()
            if self._state is ParseState.IN_TOOL_CALL:
                found = _calls_from_block(self._buffer)
                if found:
                    for call in found:
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
                found = _calls_from_block(inner)
                if found:
                    for call in found:
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
        found = _calls_from_block(body)
        if found:
            for call in found:
                self._calls.append(call)
                events.append(Event(EventKind.TOOL_CALL, call=call))
        elif body.strip():
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
