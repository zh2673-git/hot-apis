"""工具调用适配层 · 编排（数据流转-编排）

串联 `render → 上游 → parse`，并作为**请求级状态的唯一持有边界**。
本包内唯一允许接触 provider（infrastructure）的位置，且只通过参数注入。

资源回收义务（destroy 的等价物）：`stream()` 必须在 `finally` 中丢弃 parser，
保证流式中断也不残留生成级 buffer。
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List

from ..models import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    DeltaMessage,
    FunctionCallDelta,
    StreamChoice,
    ToolCallDelta,
    Usage,
)
from .parse import ToolCallStreamParser, extract, to_tool_calls
from .render import build_system_prompt, render_messages
from .spec import Event, EventKind, normalize_tools


@dataclass
class Prepared:
    request: ChatCompletionRequest      # 已把 messages 换成压平后的单条
    model: str                          # 客户端原始请求的模型名
    tools: List[dict]
    tool_choice: Any
    parse_enabled: bool
    system_prompt: str


def prepare(request: ChatCompletionRequest) -> Prepared:
    tools = normalize_tools(request.tools)
    parse_enabled = request.tool_choice != "none"
    flat_request = request.model_copy(update={
        "messages": render_messages(request.messages, tools, request.tool_choice),
        "tools": None,
        "tool_choice": None,
    })
    return Prepared(
        request=flat_request,
        model=request.model,
        tools=tools,
        tool_choice=request.tool_choice,
        parse_enabled=parse_enabled,
        system_prompt=build_system_prompt(tools, request.tool_choice),
    )


def build_response(prepared: Prepared, upstream_text: str) -> ChatCompletionResponse:
    if prepared.parse_enabled:
        content, raw_calls = extract(upstream_text)
    else:
        content, raw_calls = (upstream_text or ""), []

    calls = to_tool_calls(raw_calls)
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
        created=int(time.time()),
        model=prepared.model,
        choices=[ChatCompletionChoice(
            index=0,
            message=ChatMessage(role="assistant", content=content or None,
                                tool_calls=calls or None),
            finish_reason="tool_calls" if calls else "stop",
        )],
        usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )


async def stream(prepared: Prepared, provider, request: ChatCompletionRequest) -> AsyncGenerator[str, None]:
    parser = ToolCallStreamParser(enabled=prepared.parse_enabled)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())
    counter: Dict[str, int] = {"index": 0}

    def frame(delta: DeltaMessage, finish_reason=None) -> str:
        chunk = ChatCompletionChunk(
            id=chunk_id, created=created, model=prepared.model,
            choices=[StreamChoice(index=0, delta=delta, finish_reason=finish_reason)],
        )
        return f"data: {chunk.model_dump_json(exclude_unset=True, exclude_none=True)}\n\n"

    def frames_for(events: List[Event]) -> List[str]:
        lines: List[str] = []
        for event in events:
            if event.kind is EventKind.CONTENT and event.text:
                lines.append(frame(DeltaMessage(content=event.text)))
            elif event.kind is EventKind.TOOL_CALL:
                call = event.call or {}
                arguments = call.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if arguments is not None else {},
                                           ensure_ascii=False)
                lines.append(frame(DeltaMessage(tool_calls=[ToolCallDelta(
                    index=counter["index"],
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    type="function",
                    function=FunctionCallDelta(name=call.get("name", ""), arguments=arguments),
                )])))
                counter["index"] += 1
        return lines

    try:
        yield frame(DeltaMessage(role="assistant"))
        async for upstream in provider.chat_completion_stream(prepared.request):
            text = ""
            for choice in upstream.choices or []:
                if choice.delta and choice.delta.content:
                    text += choice.delta.content
            if not text:
                continue
            for line in frames_for(parser.feed(text)):
                yield line
        for line in frames_for(parser.finish()):
            yield line
        yield frame(DeltaMessage(), "tool_calls" if parser.has_tool_calls() else "stop")
    except Exception as exc:
        payload = json.dumps({"error": {"message": str(exc), "type": "internal_error"}})
        yield f"data: {payload}\n\n"
    finally:
        parser = None            # 生成级状态回收（destroy 的等价物）
    yield "data: [DONE]\n\n"
