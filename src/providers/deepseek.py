import httpx
import json
import base64
import asyncio
import struct
from typing import Optional, AsyncGenerator, Dict, Any, List
from pathlib import Path
import time
import uuid

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider
from .pow import solve_pow_challenge


class DeepSeekProvider(BaseProvider):
    BASE_URL = "https://chat.deepseek.com"
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self.headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "x-client-locale": "zh_CN",
            "x-client-platform": "web",
            "x-client-version": "1.7.0",
            "x-app-version": "20241129.1",
            "x-client-timezone-offset": "28800",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "origin": "https://chat.deepseek.com",
            "referer": "https://chat.deepseek.com/",
        }
        if token:
            self.headers["authorization"] = f"Bearer {token}"
    
    @property
    def name(self) -> str:
        return "deepseek"
    
    @property
    def models(self) -> List[str]:
        return [
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek",
            "deepseek-r1",
        ]
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client
    
    async def _create_session(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self.base_url}/api/v0/chat_session/create",
            headers=self.headers,
            json={}
        )
        response.raise_for_status()
        data = response.json()
        return data["data"]["biz_data"]["id"]
    
    async def _get_pow_challenge(self, client: httpx.AsyncClient, target_path: str = "/api/v0/chat/completion") -> Dict:
        response = await client.post(
            f"{self.base_url}/api/v0/chat/create_pow_challenge",
            headers=self.headers,
            json={"target_path": target_path}
        )
        response.raise_for_status()
        data = response.json()
        return data["data"]["biz_data"]["challenge"]
    
    def _solve_pow(self, challenge: Dict) -> str:
        return solve_pow_challenge(challenge)
    
    def _build_messages(self, messages: List[ChatMessage]) -> str:
        if not messages:
            return ""
        
        prompt_parts = []
        for msg in messages:
            if msg.role == "system":
                prompt_parts.append(f"[System]: {msg.content}")
            elif msg.role == "user":
                prompt_parts.append(msg.content)
            elif msg.role == "assistant":
                prompt_parts.append(f"[Assistant]: {msg.content}")
        
        return "\n".join(prompt_parts)
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        session_id = await self._create_session(client)
        challenge = await self._get_pow_challenge(client)
        pow_response = self._solve_pow(challenge)
        
        headers = {**self.headers, "x-ds-pow-response": pow_response}
        
        model_type = self._get_model_type(request.model)
        prompt = self._build_messages(request.messages)
        
        payload = {
            "chat_session_id": session_id,
            "parent_message_id": None,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": model_type == "reasoner",
            "search_enabled": False,
            "preempt": False
        }
        
        async with client.stream(
            "POST",
            f"{self.base_url}/api/v0/chat/completion",
            headers=headers,
            json=payload
        ) as response:
            response.raise_for_status()
            
            content = ""
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() and data_str != "[DONE]":
                        try:
                            data = json.loads(data_str)
                            if "v" in data:
                                v = data["v"]
                                if isinstance(v, str):
                                    content += v
                                elif isinstance(v, dict):
                                    if "response" in v:
                                        resp = v["response"]
                                        if "fragments" in resp:
                                            for frag in resp["fragments"]:
                                                if "content" in frag:
                                                    content = frag["content"]
                        except json.JSONDecodeError:
                            continue
        
        return ChatCompletionResponse(
            id=f"{session_id}@1",
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
        
        session_id = await self._create_session(client)
        challenge = await self._get_pow_challenge(client)
        pow_response = self._solve_pow(challenge)
        
        headers = {**self.headers, "x-ds-pow-response": pow_response}
        
        model_type = self._get_model_type(request.model)
        prompt = self._build_messages(request.messages)
        
        payload = {
            "chat_session_id": session_id,
            "parent_message_id": None,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": model_type == "reasoner",
            "search_enabled": False,
            "preempt": False
        }
        
        async with client.stream(
            "POST",
            f"{self.base_url}/api/v0/chat/completion",
            headers=headers,
            json=payload
        ) as response:
            response.raise_for_status()
            
            chunk_id = f"{session_id}@1"
            created = int(time.time())
            
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() and data_str != "[DONE]":
                        try:
                            data = json.loads(data_str)
                            
                            if "v" in data:
                                v = data["v"]
                                if isinstance(v, str):
                                    if v == "FINISHED":
                                        continue
                                    yield ChatCompletionChunk(
                                        id=chunk_id,
                                        model=request.model,
                                        created=created,
                                        choices=[
                                            StreamChoice(
                                                index=0,
                                                delta=DeltaMessage(content=v),
                                                finish_reason=None
                                            )
                                        ]
                                    )
                                elif isinstance(v, dict):
                                    if "response" in v:
                                        resp = v["response"]
                                        if "fragments" in resp:
                                            for frag in resp["fragments"]:
                                                if "content" in frag:
                                                    content = frag["content"]
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
                        except json.JSONDecodeError:
                            continue
        
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
    
    def _get_model_type(self, model: str) -> str:
        model_lower = model.lower()
        if "reasoner" in model_lower or "r1" in model_lower or "think" in model_lower:
            return "reasoner"
        if "v4-pro" in model_lower or "v4.5" in model_lower:
            return "reasoner"
        return "chat"
    
    def get_model_mapping(self, model: str) -> str:
        model_lower = model.lower()
        if "v4-pro" in model_lower:
            return "deepseek-chat"
        if "v4-flash" in model_lower:
            return "deepseek-chat"
        if "chat" in model_lower:
            return "deepseek-chat"
        return "deepseek-chat"
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
