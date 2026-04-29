import httpx
import json
import time
import uuid
import re
import asyncio
from typing import Optional, AsyncGenerator, Dict, Any, List
from dataclasses import dataclass

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider


@dataclass
class MetasoChatSession:
    conversation_id: str = ""
    source_id: str = ""


class MetasoProvider(BaseProvider):
    BASE_URL = "https://metaso.cn"
    
    SEARCH_MODES = {
        "fast": {"mode": "fast", "scholar": False},
        "concise": {"mode": "concise", "scholar": False},
        "detail": {"mode": "detail", "scholar": False},
        "research": {"mode": "research", "scholar": False},
        "concise-scholar": {"mode": "concise", "scholar": True},
        "detail-scholar": {"mode": "detail", "scholar": True},
        "research-scholar": {"mode": "research", "scholar": True},
        "default": "detail"
    }
    
    FAKE_HEADERS = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://metaso.cn",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    }
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._sessions: Dict[str, MetasoChatSession] = {}
        
        if token and "-" in token:
            parts = token.split("-")
            if len(parts) >= 2:
                self._uid = parts[0]
                self._sid = parts[1]
            else:
                self._uid = token
                self._sid = ""
        else:
            self._uid = ""
            self._sid = ""
    
    @property
    def name(self) -> str:
        return "metaso"
    
    @property
    def models(self) -> List[str]:
        return [
            "metaso",
            "metaso-fast",
            "metaso-concise",
            "metaso-detail",
            "metaso-research",
            "metaso-scholar",
            "metaso-concise-scholar",
            "metaso-detail-scholar",
            "metaso-research-scholar"
        ]
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=300.0)
        return self._client
    
    def _generate_cookie(self) -> str:
        if self._uid and self._sid:
            return f"uid={self._uid}; sid={self._sid}; "
        return ""
    
    def _get_cookies_dict(self) -> Dict[str, str]:
        cookies = {}
        if self._uid:
            cookies["uid"] = self._uid
        if self._sid:
            cookies["sid"] = self._sid
        return cookies
    
    def _get_search_mode(self, model: str, messages: List[ChatMessage], temperature: float = 0.6) -> Dict[str, Any]:
        model_lower = model.lower()
        
        for key, config in self.SEARCH_MODES.items():
            if key in model_lower:
                return config
        
        if messages:
            content = messages[-1].content
            
            if "学术简洁搜索" in content or "学术-简洁" in content:
                return self.SEARCH_MODES["concise-scholar"]
            elif "学术深入搜索" in content or "学术-深入" in content:
                return self.SEARCH_MODES["detail-scholar"]
            elif "学术研究搜索" in content or "学术-研究" in content:
                return self.SEARCH_MODES["research-scholar"]
            elif "学术" in content:
                return self.SEARCH_MODES["detail-scholar"]
            elif "简洁搜索" in content or "简洁" in content:
                return self.SEARCH_MODES["concise"]
            elif "深入搜索" in content or "深入" in content:
                return self.SEARCH_MODES["detail"]
            elif "研究搜索" in content or "研究" in content:
                return self.SEARCH_MODES["research"]
            
            if temperature < 0.4:
                return self.SEARCH_MODES["concise"]
            elif temperature >= 0.7:
                return self.SEARCH_MODES["research"]
        
        return self.SEARCH_MODES["detail"]
    
    def _prepare_messages(self, model: str, messages: List[ChatMessage], temperature: float = 0.6) -> Dict[str, str]:
        mode_config = self._get_search_mode(model, messages, temperature)
        mode = mode_config["mode"]
        engine_type = "scholar" if mode_config["scholar"] else ""
        
        latest_message = messages[-1] if messages else None
        if not latest_message:
            raise ValueError("No messages provided")
        
        content = latest_message.content
        
        if "天气" in content:
            content += "，直接回答"
        
        content = re.sub(r'简洁搜索[:|：]?', '', content)
        content = re.sub(r'深入搜索[:|：]?', '', content)
        content = re.sub(r'研究搜索[:|：]?', '', content)
        content = re.sub(r'学术简洁搜索[:|：]?', '', content)
        content = re.sub(r'学术深入搜索[:|：]?', '', content)
        content = re.sub(r'学术研究搜索[:|：]?', '', content)
        content = re.sub(r'^学术', '', content)
        
        content = content.strip()
        
        return {
            "mode": mode,
            "engineType": engine_type,
            "content": content
        }
    
    async def _acquire_meta_token(self, client: httpx.AsyncClient) -> str:
        headers = {
            **self.FAKE_HEADERS,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        
        response = await client.get(
            self.base_url + "/",
            headers=headers,
            cookies=self._get_cookies_dict()
        )
        
        html = response.text
        
        regex = r'<meta id="meta-token" content="([^"]*)"'
        match = re.search(regex, html)
        
        if not match or not match.group(1):
            raise ValueError("meta-token not found in page")
        
        return match.group(1)
    
    async def _create_conversation(
        self, 
        client: httpx.AsyncClient, 
        question: str, 
        mode: str, 
        engine_type: str,
        meta_token: str
    ) -> str:
        headers = {
            **self.FAKE_HEADERS,
            "Token": meta_token,
            "Is-Mini-Webview": "0",
            "Content-Type": "application/json",
        }
        
        payload = {
            "question": question,
            "mode": mode,
            "engineType": engine_type,
            "scholarSearchDomain": "all",
        }
        
        response = await client.post(
            self.base_url + "/api/session",
            headers=headers,
            json=payload,
            cookies=self._get_cookies_dict()
        )
        
        data = response.json()
        
        if "errCode" in data and data["errCode"] != 0:
            raise ValueError(f"Failed to create conversation: {data.get('errMsg', 'Unknown error')}")
        
        conv_id = data.get("data", {}).get("id", "")
        if not conv_id:
            conv_id = data.get("id", "")
        
        return conv_id
    
    def _remove_index_label(self, content: str) -> str:
        return re.sub(r'\[\[\d+\]\]', '', content)
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        temperature = request.temperature if request.temperature is not None else 0.6
        prepared = self._prepare_messages(request.model, request.messages, temperature)
        mode = prepared["mode"]
        engine_type = prepared["engineType"]
        content = prepared["content"]
        
        meta_token = await self._acquire_meta_token(client)
        
        conv_id = await self._create_conversation(
            client, content, mode, engine_type, meta_token
        )
        
        headers = {
            **self.FAKE_HEADERS,
            "Accept": "text/event-stream",
        }
        
        params = {
            "sessionId": conv_id,
            "question": content,
            "lang": "zh",
            "mode": mode,
            "url": f"{self.base_url}/search/{conv_id}?newSearch=true&q={content}",
            "enableMix": "true",
            "scholarSearchDomain": "all",
            "expectedCurrentSessionSearchCount": "1",
            "is-mini-webview": "0",
            "token": meta_token,
        }
        
        if engine_type == "scholar":
            params["scholarSearchDomain"] = "all"
        
        full_content = ""
        
        async with client.stream(
            "GET", 
            self.base_url + "/api/searchV2",
            headers=headers,
            params=params,
            cookies=self._get_cookies_dict()
        ) as response:
            response.raise_for_status()
            
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    
                    if not line or not line.startswith("data:"):
                        continue
                    
                    if line == "data: [DONE]":
                        break
                    
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    
                    if data.get("type") == "append-text":
                        text = data.get("text", "")
                        full_content += self._remove_index_label(text)
                    elif data.get("type") == "error":
                        full_content += f"[{data.get('code', 'ERROR')}]{data.get('msg', 'Unknown error')}"
        
        return ChatCompletionResponse(
            id=conv_id or f"chatcmpl-{uuid.uuid4().hex[:8]}",
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
        
        temperature = request.temperature if request.temperature is not None else 0.6
        prepared = self._prepare_messages(request.model, request.messages, temperature)
        mode = prepared["mode"]
        engine_type = prepared["engineType"]
        content = prepared["content"]
        
        meta_token = await self._acquire_meta_token(client)
        
        conv_id = await self._create_conversation(
            client, content, mode, engine_type, meta_token
        )
        
        headers = {
            **self.FAKE_HEADERS,
            "Accept": "text/event-stream",
        }
        
        params = {
            "sessionId": conv_id,
            "question": content,
            "lang": "zh",
            "mode": mode,
            "url": f"{self.base_url}/search/{conv_id}?newSearch=true&q={content}",
            "enableMix": "true",
            "scholarSearchDomain": "all",
            "expectedCurrentSessionSearchCount": "1",
            "is-mini-webview": "0",
            "token": meta_token,
        }
        
        if engine_type == "scholar":
            params["scholarSearchDomain"] = "all"
        
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())
        
        async with client.stream(
            "GET",
            self.base_url + "/api/searchV2",
            headers=headers,
            params=params,
            cookies=self._get_cookies_dict()
        ) as response:
            response.raise_for_status()
            
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    
                    if not line or not line.startswith("data:"):
                        continue
                    
                    if line == "data: [DONE]":
                        yield ChatCompletionChunk(
                            id=conv_id or chunk_id,
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
                    
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    
                    text_content = ""
                    
                    if data.get("type") == "append-text":
                        text_content = self._remove_index_label(data.get("text", ""))
                    elif data.get("type") == "error":
                        text_content = f"[{data.get('code', 'ERROR')}]{data.get('msg', 'Unknown error')}"
                    
                    if text_content:
                        yield ChatCompletionChunk(
                            id=conv_id or chunk_id,
                            model=request.model,
                            created=created,
                            choices=[
                                StreamChoice(
                                    index=0,
                                    delta=DeltaMessage(content=text_content),
                                    finish_reason=None
                                )
                            ]
                        )
    
    def get_model_mapping(self, model: str) -> str:
        return "metaso"
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
