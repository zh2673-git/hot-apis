import httpx
import json
import time
import uuid
import random
import string
import hashlib
import re
from typing import Optional, AsyncGenerator, Dict, Any, List
from dataclasses import dataclass, field

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider


def generate_device_id() -> str:
    return ''.join(random.choices(string.digits, k=19))


def generate_web_id() -> str:
    return ''.join(random.choices(string.digits, k=19))


def generate_local_id() -> str:
    return f"local_{int(time.time() * 1000)}{random.randint(100000, 999999)}"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_unique_key() -> str:
    return str(uuid.uuid4())


@dataclass
class DoubaoChatSession:
    conversation_id: str = ""
    section_id: str = ""
    local_conversation_id: str = field(default_factory=generate_local_id)


class DoubaoProvider(BaseProvider):
    BASE_URL = "https://www.doubao.com"
    
    DEFAULT_BOT_ID = "7338286299411103781"
    
    FAKE_HEADERS = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.doubao.com",
        "Referer": "https://www.doubao.com/chat/",
        "Sec-Ch-Ua": '"Chromium";v="144", "Google Chrome";v="144", "Not(A:Brand";v="8"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    }
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._device_id: str = generate_device_id()
        self._web_id: str = generate_web_id()
        self._tea_uuid: str = generate_web_id()
        self._fp: str = f"verify_{generate_uuid().replace('-', '_')[:20]}"
        self._sessions: Dict[str, DoubaoChatSession] = {}
        
    @property
    def name(self) -> str:
        return "doubao"
    
    @property
    def models(self) -> List[str]:
        return [
            "doubao",
            "doubao-pro",
            "doubao-lite",
            "doubao-pro-v1",
            "doubao-lite-4k",
            "doubao-lite-32k",
            "doubao-1.5-pro",
            "doubao-1.5-lite",
            "doubao-seedream-3",
        ]
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=300.0)
        return self._client
    
    def _get_cookies_dict(self) -> Dict[str, str]:
        cookies = {}
        if self.token:
            cookies["sessionid"] = self.token
            cookies["sessionid_ss"] = self.token
        return cookies
    
    def _get_or_create_session(self, session_key: str) -> DoubaoChatSession:
        if session_key not in self._sessions:
            self._sessions[session_key] = DoubaoChatSession()
        return self._sessions[session_key]
    
    def _build_query_params(self) -> Dict[str, str]:
        return {
            "aid": "497858",
            "device_id": self._device_id,
            "device_platform": "web",
            "fp": self._fp,
            "language": "zh",
            "pc_version": "3.5.10",
            "pkg_type": "release_version",
            "real_aid": "497858",
            "region": "",
            "samantha_web": "1",
            "sys_region": "",
            "tea_uuid": self._tea_uuid,
            "use-olympus-account": "1",
            "version_code": "20800",
            "web_id": self._web_id,
            "web_tab_id": str(uuid.uuid4()),
        }
    
    def _build_request_body(
        self, 
        messages: List[ChatMessage], 
        session: DoubaoChatSession,
        model: str = "doubao"
    ) -> Dict[str, Any]:
        local_message_id = generate_uuid()
        block_id = generate_uuid()
        
        last_user_message = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_message = msg.content
                break
        
        if not last_user_message:
            last_user_message = messages[-1].content if messages else ""
        
        request_body = {
            "client_meta": {
                "local_conversation_id": session.local_conversation_id,
                "conversation_id": session.conversation_id,
                "bot_id": self.DEFAULT_BOT_ID,
                "last_section_id": session.section_id,
                "last_message_index": None
            },
            "messages": [
                {
                    "local_message_id": local_message_id,
                    "content_block": [
                        {
                            "block_type": 10000,
                            "content": {
                                "text_block": {
                                    "text": last_user_message,
                                    "icon_url": "",
                                    "icon_url_dark": "",
                                    "summary": ""
                                },
                                "pc_event_block": ""
                            },
                            "block_id": block_id,
                            "parent_id": "",
                            "meta_info": [],
                            "append_fields": []
                        }
                    ],
                    "message_status": 0
                }
            ],
            "option": {
                "send_message_scene": "",
                "create_time_ms": int(time.time() * 1000),
                "collect_id": "",
                "is_audio": False,
                "answer_with_suggest": False,
                "tts_switch": False,
                "need_deep_think": 0,
                "click_clear_context": False,
                "from_suggest": False,
                "is_regen": False,
                "is_replace": False,
                "disable_sse_cache": False,
                "select_text_action": "",
                "resend_for_regen": False,
                "scene_type": 0,
                "unique_key": generate_unique_key(),
                "start_seq": 0,
                "need_create_conversation": not bool(session.conversation_id),
                "conversation_init_option": {"need_ack_conversation": True},
                "regen_query_id": [],
                "edit_query_id": [],
                "regen_instruction": "",
                "no_replace_for_regen": False,
                "message_from": 0,
                "shared_app_name": "",
                "sse_recv_event_options": {"support_chunk_delta": True},
                "is_ai_playground": False
            },
            "ext": {
                "use_deep_think": "0",
                "fp": self._fp,
                "conversation_init_option": '{"need_ack_conversation":true}',
                "commerce_credit_config_enable": "0",
                "sub_conv_firstmet_type": "1"
            }
        }
        
        return request_body
    
    def _parse_sse_response(self, content: str) -> List[Dict[str, Any]]:
        events = []
        lines = content.split("\n")
        current_event = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                if current_event:
                    events.append(current_event)
                    current_event = {}
                continue
            
            if line.startswith("id:"):
                current_event["id"] = line[3:].strip()
            elif line.startswith("event:"):
                current_event["event"] = line[6:].strip()
            elif line.startswith("data:"):
                data_str = line[5:].strip()
                try:
                    current_event["data"] = json.loads(data_str)
                except json.JSONDecodeError:
                    current_event["data"] = data_str
        
        if current_event:
            events.append(current_event)
        
        return events
    
    def _extract_content_from_events(self, events: List[Dict], session: DoubaoChatSession) -> str:
        content = ""
        
        for event in events:
            event_type = event.get("event", "")
            data = event.get("data", {})
            
            if event_type == "SSE_ACK":
                if isinstance(data, dict):
                    ack_meta = data.get("ack_client_meta", {})
                    if not session.conversation_id:
                        session.conversation_id = ack_meta.get("conversation_id", "")
                    if not session.section_id:
                        session.section_id = ack_meta.get("section_id", "")
            
            elif event_type == "CHUNK_DELTA":
                if isinstance(data, dict):
                    text = data.get("text", "")
                    content += text
            
            elif event_type == "STREAM_MSG_NOTIFY":
                if isinstance(data, dict):
                    meta = data.get("meta", {})
                    msg_data = data.get("content", {})
                    content_blocks = msg_data.get("content_block", [])
                    for block in content_blocks:
                        if block.get("block_type") == 10000:
                            text_block = block.get("content", {}).get("text_block", {})
                            text = text_block.get("text", "")
                            if text and not content:
                                content = text
        
        return content
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        session_key = f"{request.model}_{hash(tuple(m.content for m in request.messages))}"
        session = self._get_or_create_session(session_key)
        
        query_params = self._build_query_params()
        request_body = self._build_request_body(request.messages, session, request.model)
        
        headers = {
            **self.FAKE_HEADERS,
            "Content-Type": "application/json",
        }
        
        response = await client.post(
            f"{self.base_url}/chat/completion",
            params=query_params,
            headers=headers,
            json=request_body,
            cookies=self._get_cookies_dict()
        )
        response.raise_for_status()
        
        raw_content = response.text
        events = self._parse_sse_response(raw_content)
        content = self._extract_content_from_events(events, session)
        
        return ChatCompletionResponse(
            id=session.conversation_id or f"chatcmpl-{uuid.uuid4().hex[:8]}",
            model=request.model,
            created=int(time.time()),
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content),
                    finish_reason="stop"
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        )
    
    async def chat_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        client = await self._get_client()
        
        session_key = f"{request.model}_{hash(tuple(m.content for m in request.messages))}"
        session = self._get_or_create_session(session_key)
        
        query_params = self._build_query_params()
        request_body = self._build_request_body(request.messages, session, request.model)
        
        headers = {
            **self.FAKE_HEADERS,
            "Content-Type": "application/json",
        }
        
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())
        
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completion",
            params=query_params,
            headers=headers,
            json=request_body,
            cookies=self._get_cookies_dict()
        ) as response:
            response.raise_for_status()
            
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                while "\n\n" in buffer:
                    event_str, buffer = buffer.split("\n\n", 1)
                    
                    lines = event_str.strip().split("\n")
                    event_type = ""
                    data = {}
                    
                    for line in lines:
                        line = line.strip()
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                    
                    if event_type == "SSE_ACK":
                        if isinstance(data, dict):
                            ack_meta = data.get("ack_client_meta", {})
                            if not session.conversation_id:
                                session.conversation_id = ack_meta.get("conversation_id", "")
                                chunk_id = session.conversation_id
                            if not session.section_id:
                                session.section_id = ack_meta.get("section_id", "")
                    
                    elif event_type == "STREAM_MSG_NOTIFY":
                        if isinstance(data, dict):
                            content = data.get("content", {})
                            content_blocks = content.get("content_block", [])
                            for block in content_blocks:
                                if block.get("block_type") == 10000:
                                    text_block = block.get("content", {}).get("text_block", {})
                                    text = text_block.get("text", "")
                                    if text:
                                        yield ChatCompletionChunk(
                                            id=chunk_id,
                                            model=request.model,
                                            created=created,
                                            choices=[
                                                StreamChoice(
                                                    index=0,
                                                    delta=DeltaMessage(content=text),
                                                    finish_reason=None
                                                )
                                            ]
                                        )
                    
                    elif event_type == "CHUNK_DELTA":
                        if isinstance(data, dict):
                            text = data.get("text", "")
                            if text:
                                yield ChatCompletionChunk(
                                    id=chunk_id,
                                    model=request.model,
                                    created=created,
                                    choices=[
                                        StreamChoice(
                                            index=0,
                                            delta=DeltaMessage(content=text),
                                            finish_reason=None
                                        )
                                    ]
                                )
                    
                    elif event_type == "SSE_REPLY_END":
                        yield ChatCompletionChunk(
                            id=chunk_id,
                            model=request.model,
                            created=created,
                            choices=[
                                StreamChoice(
                                    index=0,
                                    delta=DeltaMessage(),
                                    finish_reason="stop"
                                )
                            ]
                        )
                        return
    
    def get_model_mapping(self, model: str) -> str:
        model_lower = model.lower()
        if "seedream" in model_lower:
            return "Doubao-Seed-2-0-Pro"
        if "pro-v1" in model_lower or "1.5-pro" in model_lower:
            return "Doubao-pro"
        if "lite-4k" in model_lower or "lite-32k" in model_lower:
            return "Doubao-lite"
        if "lite" in model_lower:
            return "Doubao-lite"
        if "pro" in model_lower:
            return "Doubao-pro"
        return "doubao"
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
