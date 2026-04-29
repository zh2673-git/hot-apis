import asyncio
import httpx
import json
import time
import uuid
import hashlib
import base64
from typing import Optional, AsyncGenerator, Dict, Any, List
from urllib.parse import urlencode, quote

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider


def md5_hash(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_uuid_no_dash() -> str:
    return uuid.uuid4().hex


def decode_jwt_payload(token: str) -> Dict:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


class MiniMaxProvider(BaseProvider):
    BASE_URL = "https://agent.minimaxi.com"
    
    SIGNATURE_SECRET = "I*7Cf%WZ#S&%1RlZJ&C2"
    YY_SUFFIX = "ooui"
    
    FAKE_HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://agent.minimaxi.com",
        "Referer": "https://agent.minimaxi.com/",
        "Sec-Ch-Ua": '"Chromium";v="144", "Google Chrome";v="144", "Not(A:Brand";v="8"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    }
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._uuid: str = generate_uuid()
        self._user_id: str = ""
        self._device_id: str = ""
        if token:
            payload = decode_jwt_payload(token)
            user = payload.get("user", {})
            self._user_id = user.get("id", "")
            self._device_id = user.get("deviceID", "")
    
    @property
    def name(self) -> str:
        return "minimax"
    
    @property
    def models(self) -> List[str]:
        return [
            "minimax",
            "minimax-auto",
            "MiniMax-M2.5",
            "MiniMax-M2.7",
        ]
    
    def _get_model_option(self, model: str) -> Dict[str, Any]:
        model_lower = model.lower()
        if "auto" in model_lower or model_lower == "minimax":
            return {"display_name": "Auto", "model_type": 0}
        elif "m2.7" in model_lower:
            return {"display_name": "MiniMax-M2.7", "model_type": 502}
        elif "m2.5" in model_lower:
            return {"display_name": "MiniMax-M2.5", "model_type": 501}
        else:
            return {"display_name": "MiniMax-M2.5", "model_type": 501}
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client
    
    def _generate_signature(self, timestamp: int, body: str) -> str:
        sign_str = f"{timestamp}{self.SIGNATURE_SECRET}{body}"
        return md5_hash(sign_str)
    
    def _generate_yy(self, time_ms: int, body: Dict, has_search_params_path: str, method: str) -> str:
        body_str = "{}"
        if method and method.lower() == "post":
            body_str = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        
        time_str = str(time_ms)
        time_md5 = md5_hash(time_str)
        
        yy_str = f"{quote(has_search_params_path, safe='')}_{body_str}{time_md5}{self.YY_SUFFIX}"
        return md5_hash(yy_str)
    
    def _build_params(self, timestamp: int) -> Dict[str, Any]:
        params = {
            "device_platform": "web",
            "biz_id": 3,
            "app_id": 3001,
            "version_code": 22201,
            "unix": timestamp * 1000,
            "timezone_offset": 28800,
            "lang": "zh",
            "uuid": self._uuid,
            "device_id": self._device_id,
            "os_name": "Windows",
            "browser_name": "chrome",
            "device_memory": 8,
            "cpu_core_num": 32,
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "user_id": self._user_id,
            "screen_width": 1600,
            "screen_height": 1000,
            "token": self.token,
            "client": "web",
        }
        return params
    
    def _build_has_search_params_path(self, url: str, params: Dict) -> str:
        query_string = urlencode(params)
        return f"{url}?{query_string}" if query_string else url
    
    def _prepare_messages(self, messages: List[ChatMessage]) -> str:
        text_parts = []
        for msg in messages:
            content = msg.content
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
        return "\n".join(text_parts)
    
    def _parse_stream_response(self, line: str) -> Optional[Dict]:
        if not line:
            return None
        
        line = line.strip()
        
        if line.startswith("data: "):
            data_str = line[6:].strip()
            if not data_str or data_str == "[DONE]":
                return None
            try:
                return json.loads(data_str)
            except json.JSONDecodeError:
                return None
        
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    
    async def _create_chat(self, client: httpx.AsyncClient, headers: Dict, params: Dict) -> Optional[str]:
        try:
            response = await client.post(
                f"{self.base_url}/matrix/api/v1/chat/create_chat",
                headers=headers,
                params=params,
                json={}
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("chat_id")
        except Exception:
            pass
        return None
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        if not self.token:
            raise ValueError("MiniMax token is required")
        
        timestamp = int(time.time())
        time_ms = timestamp * 1000
        
        params = self._build_params(timestamp)
        
        text = self._prepare_messages(request.messages)
        
        body = {
            "msg_type": 1,
            "text": text,
            "chat_type": 2,
            "attachments": [],
            "selected_mcp_tools": [],
            "sub_agent_ids": [],
            "model_option": self._get_model_option(request.model)
        }
        
        body_str = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        
        signature = self._generate_signature(timestamp, body_str)
        
        url_path = "/matrix/api/v1/chat/send_msg"
        has_search_params_path = self._build_has_search_params_path(url_path, params)
        yy = self._generate_yy(time_ms, body, has_search_params_path, "POST")
        
        headers = {
            **self.FAKE_HEADERS,
            "Content-Type": "application/json",
            "token": self.token,
            "x-timestamp": str(timestamp),
            "x-signature": signature,
            "yy": yy,
        }
        
        response = await client.post(
            f"{self.base_url}{url_path}",
            headers=headers,
            params=params,
            json=body
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"MiniMax API error: {response.status_code} - {response.text}")
        
        data = response.json()
        
        if data.get("base_resp", {}).get("status_code") != 0:
            raise RuntimeError(f"MiniMax API error: {data.get('base_resp', {}).get('status_msg', 'Unknown error')}")
        
        chat_id = data.get("chat_id")
        msg_id = data.get("msg_id")
        
        content = ""
        max_wait = 60
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            await asyncio.sleep(1)
            
            detail_params = self._build_params(int(time.time()))
            detail_params["token"] = self.token
            
            detail_body = {"chat_id": chat_id, "size": 500, "desc": True}
            detail_body_str = json.dumps(detail_body, ensure_ascii=False, separators=(',', ':'))
            
            detail_timestamp = int(time.time())
            detail_signature = self._generate_signature(detail_timestamp, detail_body_str)
            
            detail_url_path = "/matrix/api/v1/chat/get_chat_detail"
            detail_has_search_params_path = self._build_has_search_params_path(detail_url_path, detail_params)
            detail_yy = self._generate_yy(detail_timestamp * 1000, detail_body, detail_has_search_params_path, "POST")
            
            detail_headers = {
                **self.FAKE_HEADERS,
                "Content-Type": "application/json",
                "token": self.token,
                "x-timestamp": str(detail_timestamp),
                "x-signature": detail_signature,
                "yy": detail_yy,
            }
            
            detail_response = await client.post(
                f"{self.base_url}{detail_url_path}",
                headers=detail_headers,
                params=detail_params,
                json=detail_body
            )
            
            if detail_response.status_code == 200:
                detail_data = detail_response.json()
                messages = detail_data.get("messages", [])
                
                for msg in messages:
                    if msg.get("msg_type") == 2:
                        msg_content = msg.get("msg_content", "")
                        if msg_content:
                            content = msg_content
                            break
                
                if content:
                    break
        
        return ChatCompletionResponse(
            id=str(msg_id) if msg_id else generate_uuid(),
            model=request.model,
            created=timestamp,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content),
                    finish_reason="stop"
                )
            ],
            usage=Usage(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0
            )
        )
    
    async def chat_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        client = await self._get_client()
        
        if not self.token:
            raise ValueError("MiniMax token is required")
        
        timestamp = int(time.time())
        time_ms = timestamp * 1000
        
        params = self._build_params(timestamp)
        params["token"] = self.token
        
        text = self._prepare_messages(request.messages)
        
        body = {
            "msg_type": 1,
            "text": text,
            "chat_type": 2,
            "attachments": [],
            "selected_mcp_tools": [],
            "sub_agent_ids": [],
            "model_option": self._get_model_option(request.model)
        }
        
        body_str = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        
        signature = self._generate_signature(timestamp, body_str)
        
        url_path = "/matrix/api/v1/chat/send_msg"
        has_search_params_path = self._build_has_search_params_path(url_path, params)
        yy = self._generate_yy(time_ms, body, has_search_params_path, "POST")
        
        headers = {
            **self.FAKE_HEADERS,
            "Content-Type": "application/json",
            "token": self.token,
            "x-timestamp": str(timestamp),
            "x-signature": signature,
            "yy": yy,
        }
        
        response = await client.post(
            f"{self.base_url}{url_path}",
            headers=headers,
            params=params,
            json=body
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"MiniMax API error: {response.status_code} - {response.text}")
        
        data = response.json()
        
        if data.get("base_resp", {}).get("status_code") != 0:
            raise RuntimeError(f"MiniMax API error: {data.get('base_resp', {}).get('status_msg', 'Unknown error')}")
        
        chat_id = data.get("chat_id")
        msg_id = data.get("msg_id")
        chunk_id = str(msg_id) if msg_id else generate_uuid()
        created = timestamp
        
        last_content = ""
        max_wait = 120
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            await asyncio.sleep(0.5)
            
            detail_params = self._build_params(int(time.time()))
            detail_params["token"] = self.token
            
            detail_body = {"chat_id": chat_id, "size": 500, "desc": True}
            detail_body_str = json.dumps(detail_body, ensure_ascii=False, separators=(',', ':'))
            
            detail_timestamp = int(time.time())
            detail_signature = self._generate_signature(detail_timestamp, detail_body_str)
            
            detail_url_path = "/matrix/api/v1/chat/get_chat_detail"
            detail_has_search_params_path = self._build_has_search_params_path(detail_url_path, detail_params)
            detail_yy = self._generate_yy(detail_timestamp * 1000, detail_body, detail_has_search_params_path, "POST")
            
            detail_headers = {
                **self.FAKE_HEADERS,
                "Content-Type": "application/json",
                "token": self.token,
                "x-timestamp": str(detail_timestamp),
                "x-signature": detail_signature,
                "yy": detail_yy,
            }
            
            detail_response = await client.post(
                f"{self.base_url}{detail_url_path}",
                headers=detail_headers,
                params=detail_params,
                json=detail_body
            )
            
            if detail_response.status_code == 200:
                detail_data = detail_response.json()
                messages = detail_data.get("messages", [])
                
                for msg in messages:
                    if msg.get("msg_type") == 2:
                        msg_content = msg.get("msg_content", "")
                        if msg_content and msg_content != last_content:
                            new_content = msg_content[len(last_content):]
                            last_content = msg_content
                            
                            yield ChatCompletionChunk(
                                id=chunk_id,
                                created=created,
                                model=request.model,
                                choices=[
                                    StreamChoice(
                                        index=0,
                                        delta=DeltaMessage(content=new_content),
                                        finish_reason=None
                                    )
                                ]
                            )
                        break
                
                chat_status = detail_data.get("chat", {}).get("chat_status")
                if chat_status == 2:
                    break
        
        yield ChatCompletionChunk(
            id=chunk_id,
            created=created,
            model=request.model,
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaMessage(),
                    finish_reason="stop"
                )
            ]
        )
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
