import httpx
import json
import time
import uuid
from typing import Optional, AsyncGenerator, Dict, Any, List
from dataclasses import dataclass
from urllib.parse import unquote

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider


def generate_request_id() -> str:
    return uuid.uuid4().hex


@dataclass
class QwenChatSession:
    session_id: str = ""
    parent_msg_id: str = "0"


class QwenProvider(BaseProvider):
    BASE_URL = "https://qianwen.biz.aliyun.com/dialog"
    
    FAKE_HEADERS = {
        "Accept": "text/event-stream",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.qianwen.com",
        "Referer": "https://www.qianwen.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "X-Platform": "pc_tongyi",
    }
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._sessions: Dict[str, QwenChatSession] = {}
        self._cookies: Dict[str, str] = {}
        self._xsrf_token: str = ""
        
        if token:
            self._parse_token(token)
    
    @property
    def name(self) -> str:
        return "qwen"
    
    @property
    def models(self) -> List[str]:
        return [
            "qwen",
            "qwen3",
            "qwen3.5-plus",
            "qwen3.6-plus",
            "qwen3-max",
            "qwen3-max-thinking",
            "qwen3-flash",
            "qwen3-coder",
            "qwen-vl-plus",
            "qwen-vl-max",
            "qwen-long",
        ]
    
    def _parse_token(self, token: str):
        if "=" in token and ";" in token:
            for part in token.split(";"):
                part = part.strip()
                if "=" in part:
                    idx = part.find("=")
                    key = part[:idx].strip()
                    value = part[idx + 1:].strip()
                    self._cookies[key] = value
                    if key == "XSRF-TOKEN":
                        self._xsrf_token = unquote(value)
        elif "=" in token:
            idx = token.find("=")
            key = token[:idx].strip()
            value = token[idx + 1:].strip()
            self._cookies[key] = value
            if key == "XSRF-TOKEN":
                self._xsrf_token = unquote(value)
        else:
            self._cookies["tongyi_sso_ticket"] = token
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=300.0)
        return self._client
    
    def _get_cookies_dict(self) -> Dict[str, str]:
        return self._cookies
    
    def _get_or_create_session(self, session_key: str) -> QwenChatSession:
        if session_key not in self._sessions:
            self._sessions[session_key] = QwenChatSession()
        return self._sessions[session_key]
    
    def _build_request_body(
        self,
        prompt: str,
        session: QwenChatSession,
        model: str = "",
        session_type: str = "text_chat"
    ) -> Dict[str, Any]:
        return {
            "action": "next",
            "contents": [
                {
                    "contentType": "text",
                    "content": prompt,
                    "role": "user"
                }
            ],
            "mode": "chat",
            "model": model,
            "requestId": generate_request_id(),
            "parentMsgId": session.parent_msg_id,
            "sessionId": session.session_id,
            "sessionType": session_type,
            "userAction": "chat"
        }
    
    def _build_headers(self) -> Dict[str, str]:
        headers = dict(self.FAKE_HEADERS)
        if self._xsrf_token:
            headers["X-Xsrf-Token"] = self._xsrf_token
        return headers
    
    def _extract_content_from_response(self, data: Dict) -> str:
        content = ""
        if "contents" in data and data["contents"]:
            for item in data["contents"]:
                if item.get("contentType") in ["text", "text2image"]:
                    content += item.get("content", "")
        return content
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        session_key = f"{request.model}_{hash(tuple(m.content for m in request.messages))}"
        session = self._get_or_create_session(session_key)
        
        last_user_message = ""
        for msg in reversed(request.messages):
            if msg.role == "user":
                last_user_message = msg.content
                break
        
        if not last_user_message:
            last_user_message = request.messages[-1].content if request.messages else ""
        
        model_code = self.get_model_mapping(request.model)
        request_body = self._build_request_body(last_user_message, session, model_code)
        headers = self._build_headers()
        
        full_content = ""
        msg_id = ""
        
        async with client.stream(
            "POST",
            self.base_url + "/conversation",
            headers=headers,
            json=request_body,
            cookies=self._get_cookies_dict()
        ) as response:
            response.raise_for_status()
            
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    
                    if not line:
                        continue
                    
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        
                        if data.get("errorCode"):
                            raise RuntimeError(f"Qwen API error: {data}")
                        
                        if "msgId" in data:
                            msg_id = data["msgId"]
                            session.parent_msg_id = msg_id
                        
                        if "sessionId" in data:
                            session.session_id = data["sessionId"]
                        
                        content = self._extract_content_from_response(data)
                        if content:
                            full_content = content
        
        return ChatCompletionResponse(
            id=msg_id or f"chatcmpl-{uuid.uuid4().hex[:8]}",
            model=request.model,
            created=int(time.time()),
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=full_content),
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
        
        last_user_message = ""
        for msg in reversed(request.messages):
            if msg.role == "user":
                last_user_message = msg.content
                break
        
        if not last_user_message:
            last_user_message = request.messages[-1].content if request.messages else ""
        
        model_code = self.get_model_mapping(request.model)
        request_body = self._build_request_body(last_user_message, session, model_code)
        headers = self._build_headers()
        
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())
        last_content = ""
        
        async with client.stream(
            "POST",
            self.base_url + "/conversation",
            headers=headers,
            json=request_body,
            cookies=self._get_cookies_dict()
        ) as response:
            response.raise_for_status()
            
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    
                    if not line:
                        continue
                    
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if not data_str:
                            continue
                        
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        
                        if data.get("errorCode"):
                            continue
                        
                        if "msgId" in data:
                            chunk_id = data["msgId"]
                            session.parent_msg_id = data["msgId"]
                        
                        if "sessionId" in data:
                            session.session_id = data["sessionId"]
                        
                        content = self._extract_content_from_response(data)
                        
                        if data.get("msgStatus") == "finish":
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
                        
                        if content and content != last_content:
                            delta_content = content[len(last_content):]
                            last_content = content
                            if delta_content:
                                yield ChatCompletionChunk(
                                    id=chunk_id,
                                    model=request.model,
                                    created=created,
                                    choices=[
                                        StreamChoice(
                                            index=0,
                                            delta=DeltaMessage(content=delta_content),
                                            finish_reason=None
                                        )
                                    ]
                                )
    
    def get_model_mapping(self, model: str) -> str:
        model_map = {
            "qwen": "Qwen",
            "qwen3": "Qwen",
            "qwen3.5-plus": "Qwen3.5-Plus",
            "qwen3.6-plus": "Qwen3.6-Plus",
            "qwen3-max": "Qwen3-Max",
            "qwen3-max-thinking": "Qwen3-Max-Thinking-Preview",
            "qwen3-flash": "Qwen3-Flash",
            "qwen3-coder": "Qwen3-Coder",
            "qwen-vl-plus": "Qwen-VL-Max",
            "qwen-vl-max": "Qwen-VL-Max",
            "qwen-long": "Qwen-Long",
        }
        return model_map.get(model.lower(), "Qwen")
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
