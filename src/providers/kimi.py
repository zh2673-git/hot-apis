import httpx
import json
import struct
import time
import uuid
import random
import string
from typing import Optional, AsyncGenerator, Dict, Any, List
from dataclasses import dataclass, field

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider


def generate_device_id() -> str:
    return ''.join(random.choices(string.digits, k=19))


def generate_session_id() -> str:
    timestamp = int(time.time() * 1000)
    random_part = random.randint(1000000000, 9999999999)
    return f"{timestamp}{random_part}"


def encode_connect_message(data: dict) -> bytes:
    json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    length = len(json_data)
    return b'\x00' + struct.pack('>I', length) + json_data


def decode_connect_stream(data: bytes) -> List[dict]:
    messages = []
    offset = 0
    while offset < len(data):
        if offset + 5 > len(data):
            break
        
        frame_type = data[offset]
        if frame_type == 0x00:
            if offset + 5 > len(data):
                break
            length = struct.unpack('>I', data[offset+1:offset+5])[0]
            if offset + 5 + length > len(data):
                break
            json_data = data[offset+5:offset+5+length]
            try:
                msg = json.loads(json_data.decode('utf-8'))
                messages.append(msg)
            except json.JSONDecodeError:
                pass
            offset += 5 + length
        elif frame_type == 0x02:
            if offset + 5 > len(data):
                break
            length = struct.unpack('>I', data[offset+1:offset+5])[0]
            if offset + 5 + length > len(data):
                break
            error_data = data[offset+5:offset+5+length]
            try:
                error_msg = json.loads(error_data.decode('utf-8'))
                messages.append({"error": error_msg})
            except json.JSONDecodeError:
                pass
            offset += 5 + length
        else:
            offset += 1
    
    return messages


@dataclass
class KimiChatSession:
    chat_id: str = ""
    last_message_id: str = ""


class KimiProvider(BaseProvider):
    BASE_URL = "https://www.kimi.com"
    
    SCENARIOS = {
        "k2.6": "SCENARIO_K2D6",
        "k2.5": "SCENARIO_K2D5",
        "k2": "SCENARIO_K2",
        "k1.5": "SCENARIO_K1D5",
        "default": "SCENARIO_K2D6"
    }
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._device_id: str = generate_device_id()
        self._session_id: str = generate_session_id()
        self._traffic_id: str = ""
        self._sessions: Dict[str, KimiChatSession] = {}
        
        self.headers = {
            "accept": "*/*",
            "content-type": "application/connect+json",
            "x-msh-platform": "web",
            "x-msh-device-id": self._device_id,
            "x-msh-version": "1.0.0",
            "x-language": "zh-CN",
            "r-timezone": "Asia/Shanghai",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "origin": "https://www.kimi.com",
            "referer": "https://www.kimi.com/",
            "connect-protocol-version": "1",
        }
        if token:
            self.headers["authorization"] = f"Bearer {token}"
    
    @property
    def name(self) -> str:
        return "kimi"
    
    @property
    def models(self) -> List[str]:
        return [
            "kimi",
            "kimi-k2.6",
            "kimi-k2.6-code",
            "kimi-k2.5",
            "kimi-k2",
            "kimi-k1.5",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
        ]
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client
    
    async def _get_user_info(self, client: httpx.AsyncClient) -> Dict:
        headers = {
            **self.headers,
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "x-msh-session-id": self._session_id,
        }
        
        response = await client.get(
            f"{self.base_url}/api/user",
            headers=headers,
            params={"t": str(int(time.time() * 1000))}
        )
        response.raise_for_status()
        data = response.json()
        self._traffic_id = data.get("id", "")
        return data
    
    async def _register_device(self, client: httpx.AsyncClient):
        headers = {
            **self.headers,
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "x-msh-session-id": self._session_id,
        }
        
        await client.post(
            f"{self.base_url}/api/device/register",
            headers=headers,
            json={}
        )
    
    def _get_scenario(self, model: str) -> str:
        model_lower = model.lower()
        for key, scenario in self.SCENARIOS.items():
            if key in model_lower:
                return scenario
        return self.SCENARIOS["default"]
    
    def _get_or_create_session(self, session_key: str) -> KimiChatSession:
        if session_key not in self._sessions:
            self._sessions[session_key] = KimiChatSession()
        return self._sessions[session_key]
    
    def _build_chat_request(self, messages: List[ChatMessage], model: str, 
                            session: KimiChatSession, thinking: bool = False) -> bytes:
        scenario = self._get_scenario(model)
        
        last_user_message = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_message = msg.content
                break
        
        if not last_user_message:
            last_user_message = messages[-1].content if messages else ""
        
        request_data = {
            "scenario": scenario,
            "tools": [{"type": "TOOL_TYPE_SEARCH", "search": {}}],
            "message": {
                "role": "user",
                "blocks": [{"message_id": "", "text": {"content": last_user_message}}],
                "scenario": scenario
            },
            "options": {"thinking": thinking}
        }
        
        if session.chat_id:
            request_data["chat_id"] = session.chat_id
            request_data["message"]["parent_id"] = session.last_message_id
        
        return encode_connect_message(request_data)
    
    def _process_stream_messages(self, messages: List[dict], session: KimiChatSession) -> str:
        content = ""
        
        for msg in messages:
            if "error" in msg:
                error_data = msg["error"]
                if error_data:
                    raise RuntimeError(f"Kimi API error: {error_data}")
                continue
            
            if "chat" in msg:
                chat_data = msg["chat"]
                if not session.chat_id:
                    session.chat_id = chat_data.get("id", session.chat_id)
            
            if "message" in msg:
                msg_data = msg["message"]
                if msg_data.get("role") == "assistant":
                    session.last_message_id = msg_data.get("id", session.last_message_id)
            
            if "block" in msg:
                block = msg["block"]
                if "text" in block:
                    text_content = block["text"].get("content", "")
                    op = msg.get("op", "")
                    if op == "set":
                        content = text_content
                    elif op == "append":
                        content += text_content
        
        return content
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        await self._get_user_info(client)
        await self._register_device(client)
        
        session_key = f"{request.model}_{hash(tuple(m.content for m in request.messages))}"
        session = self._get_or_create_session(session_key)
        
        headers = {
            **self.headers,
            "x-msh-session-id": self._session_id,
            "x-traffic-id": self._traffic_id,
        }
        
        request_body = self._build_chat_request(request.messages, request.model, session)
        
        response = await client.post(
            f"{self.base_url}/apiv2/kimi.gateway.chat.v1.ChatService/Chat",
            headers=headers,
            content=request_body
        )
        response.raise_for_status()
        
        raw_data = response.content
        messages = decode_connect_stream(raw_data)
        
        content = self._process_stream_messages(messages, session)
        
        return ChatCompletionResponse(
            id=session.last_message_id or f"chatcmpl-{uuid.uuid4().hex[:8]}",
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
        
        await self._get_user_info(client)
        await self._register_device(client)
        
        session_key = f"{request.model}_{hash(tuple(m.content for m in request.messages))}"
        session = self._get_or_create_session(session_key)
        
        headers = {
            **self.headers,
            "x-msh-session-id": self._session_id,
            "x-traffic-id": self._traffic_id,
        }
        
        request_body = self._build_chat_request(request.messages, request.model, session)
        
        async with client.stream(
            "POST",
            f"{self.base_url}/apiv2/kimi.gateway.chat.v1.ChatService/Chat",
            headers=headers,
            content=request_body
        ) as response:
            response.raise_for_status()
            
            buffer = b""
            chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
            created = int(time.time())
            
            async for chunk in response.aiter_bytes():
                buffer += chunk
                
                while len(buffer) >= 5:
                    frame_type = buffer[0]
                    
                    if frame_type not in (0x00, 0x02):
                        buffer = buffer[1:]
                        continue
                    
                    length = struct.unpack('>I', buffer[1:5])[0]
                    if len(buffer) < 5 + length:
                        break
                    
                    msg_data = buffer[5:5+length]
                    buffer = buffer[5+length:]
                    
                    if frame_type == 0x02:
                        continue
                    
                    try:
                        msg = json.loads(msg_data.decode('utf-8'))
                    except json.JSONDecodeError:
                        continue
                    
                    if "error" in msg:
                        continue
                    
                    if "chat" in msg:
                        chat_data = msg["chat"]
                        if not session.chat_id:
                            session.chat_id = chat_data.get("id", "")
                    
                    if "message" in msg:
                        msg_obj = msg["message"]
                        if msg_obj.get("role") == "assistant":
                            session.last_message_id = msg_obj.get("id", "")
                            chunk_id = session.last_message_id
                    
                    if "block" in msg:
                        block = msg["block"]
                        if "text" in block:
                            content = block["text"].get("content", "")
                            if content:
                                yield ChatCompletionChunk(
                                    id=chunk_id,
                                    model=request.model,
                                    created=created,
                                    choices=[
                                        StreamChoice(
                                            index=0,
                                            delta=DeltaMessage(content=content),
                                            finish_reason=None
                                        )
                                    ]
                                )
                    
                    if "done" in msg:
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
        return "kimi"
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
