import httpx
import json
import time
import uuid
import re
import hashlib
import base64
from typing import Optional, AsyncGenerator, Dict, Any, List
from dataclasses import dataclass

from ..models import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk
from ..models import ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage
from .base import BaseProvider


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_uuid_no_dash() -> str:
    return uuid.uuid4().hex


def generate_timestamp() -> str:
    e = int(time.time() * 1000)
    t = str(e)
    A = len(t)
    o = [int(c) for c in t]
    a = sum(o) - o[A-2]
    i = a % 10
    return t[:A-2] + str(i) + t[A-1:]


def generate_sign() -> Dict[str, str]:
    timestamp = generate_timestamp()
    x_nonce = uuid.uuid4().hex
    secret = "8a1317a7468aa3ad86e997d08f3f31cb"
    message = f"{timestamp}-{x_nonce}-{secret}"
    sign = hashlib.md5(message.encode()).hexdigest()
    return {
        "timestamp": timestamp,
        "x_nonce": x_nonce,
        "sign": sign
    }


def decode_jwt_payload(token: str) -> Optional[Dict]:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def get_token_type(token: str) -> Optional[str]:
    payload = decode_jwt_payload(token)
    if payload:
        return payload.get("type")
    return None


@dataclass
class ZhipuTokenInfo:
    access_token: str
    refresh_token: str
    refresh_time: int


class ZhipuProvider(BaseProvider):
    BASE_URL = "https://chatglm.cn"
    
    DEFAULT_ASSISTANT_ID = "65940acff94777010aa6b796"
    
    ACCESS_TOKEN_EXPIRES = 3600
    
    FAKE_HEADERS = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "App-Name": "chatglm",
        "Origin": "https://chatglm.cn",
        "Platform": "pc",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Version": "0.0.1",
    }
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(token=token, base_url=base_url or self.BASE_URL)
        self._client: Optional[httpx.AsyncClient] = None
        self._token_cache: Dict[str, ZhipuTokenInfo] = {}
    
    @property
    def name(self) -> str:
        return "zhipu"
    
    @property
    def models(self) -> List[str]:
        return [
            "zhipu",
            "chatglm",
            "glm-4",
            "glm-4-plus",
            "glm-4-air",
            "glm-4-airx",
            "glm-4-flash",
            "glm-4-long",
            "glm-4v",
            "glm-4v-plus",
            "glm-5",
            "glm-5-plus",
            "glm-5.1",
            "glm-5.1-plus",
        ]
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client
    
    async def _request_access_token(self, refresh_token: str) -> ZhipuTokenInfo:
        client = await self._get_client()
        
        token_payload = decode_jwt_payload(refresh_token)
        device_id = token_payload.get("device_id") if token_payload else generate_uuid_no_dash()
        
        sign_info = generate_sign()
        request_id = generate_uuid_no_dash()
        
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Authorization": f"Bearer {refresh_token}",
            "Content-Type": "application/json;charset=utf-8",
            "App-Name": "chatglm",
            "Origin": "https://chatglm.cn",
            "Referer": "https://chatglm.cn/main/alltoolsdetail",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "X-Device-Id": device_id,
            "X-App-Platform": "pc",
            "X-App-Version": "0.0.1",
            "X-App-fr": "default",
            "X-Lang": "zh",
            "X-Request-Id": request_id,
            "X-Exp-Groups": "",
            "X-Device-Model": "",
            "X-Device-Brand": "",
            "X-Timestamp": sign_info["timestamp"],
            "X-Nonce": sign_info["x_nonce"],
            "X-Sign": sign_info["sign"],
        }
        
        response = await client.post(
            f"{self.base_url}/chatglm/user-api/user/refresh",
            headers=headers,
            json={}
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") != 0:
            raise RuntimeError(f"Failed to refresh token: {data.get('message', 'Unknown error')}")
        
        result = data.get("result", {})
        access_token = result.get("access_token")
        new_refresh_token = result.get("refresh_token", refresh_token)
        
        if not access_token:
            raise RuntimeError("No access token in response")
        
        return ZhipuTokenInfo(
            access_token=access_token,
            refresh_token=new_refresh_token,
            refresh_time=int(time.time()) + self.ACCESS_TOKEN_EXPIRES
        )
    
    async def _get_access_token(self, token: str) -> str:
        token_type = get_token_type(token)
        
        if token_type == "access":
            return token
        
        if token_type == "refresh":
            token_info = self._token_cache.get(token)
            
            if not token_info or time.time() > token_info.refresh_time:
                token_info = await self._request_access_token(token)
                self._token_cache[token] = token_info
            
            return token_info.access_token
        
        return token
    
    async def _delete_conversation(
        self, 
        conversation_id: str, 
        access_token: str,
        assistant_id: str = None
    ):
        client = await self._get_client()
        
        headers = {
            **self.FAKE_HEADERS,
            "Authorization": f"Bearer {access_token}",
            "Referer": "https://chatglm.cn/main/alltoolsdetail",
            "X-Device-Id": generate_uuid_no_dash(),
            "X-Request-Id": generate_uuid_no_dash(),
        }
        
        try:
            await client.post(
                f"{self.base_url}/chatglm/backend-api/assistant/conversation/delete",
                headers=headers,
                json={
                    "assistant_id": assistant_id or self.DEFAULT_ASSISTANT_ID,
                    "conversation_id": conversation_id,
                }
            )
        except Exception:
            pass
    
    def _prepare_messages(self, messages: List[ChatMessage], has_conversation: bool = False) -> List[Dict]:
        if has_conversation or len(messages) < 2:
            content = ""
            for msg in messages:
                if isinstance(msg.content, str):
                    content += msg.content + "\n"
            return [{"role": "user", "content": [{"type": "text", "text": content.strip()}]}]
        
        has_file_or_image = False
        last_message = messages[-1]
        
        content_parts = []
        text_content = ""
        
        for msg in messages:
            role = msg.role
            if role == "system":
                text_content += f"<|system|>\n{msg.content}\n"
            elif role == "assistant":
                text_content += f"</s>\n{msg.content}\n"
            elif role == "user":
                text_content += f"<|user|>\n{msg.content}\n"
        
        text_content += "</s>\n"
        
        text_content = re.sub(r'\!\[.+\]\(.+\)', '', text_content)
        text_content = re.sub(r'/mnt/data/.+', '', text_content)
        
        content_parts.append({"type": "text", "text": text_content.strip()})
        
        return [{"role": "user", "content": content_parts}]
    
    def _parse_stream_response(self, line: str) -> Optional[Dict]:
        if not line or not line.startswith("data: "):
            return None
        
        data_str = line[6:].strip()
        if not data_str or data_str == "[DONE]":
            return None
        
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            return None
    
    def _extract_content_from_event(self, event: Dict) -> str:
        content = ""
        parts = event.get("parts", [])
        for part in parts:
            if part.get("role") == "assistant":
                part_content = part.get("content", [])
                for item in part_content:
                    if item.get("type") == "text":
                        content += item.get("text", "")
                    elif item.get("type") == "think":
                        think_text = item.get("text", "")
                        if think_text:
                            content += f"<think:{think_text}>"
        return content
    
    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        client = await self._get_client()
        
        if not self.token:
            raise ValueError("Zhipu token (refresh_token) is required")
        
        access_token = await self._get_access_token(self.token)
        
        token_payload = decode_jwt_payload(access_token)
        device_id = token_payload.get("device_id") if token_payload else generate_uuid_no_dash()
        
        sign_info = generate_sign()
        request_id = generate_uuid_no_dash()
        
        headers = {
            "Accept": "text/event-stream",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "App-Name": "chatglm",
            "Origin": "https://chatglm.cn",
            "Referer": "https://chatglm.cn/main/alltoolsdetail",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "X-Device-Id": device_id,
            "X-App-Platform": "pc",
            "X-App-Version": "0.0.1",
            "X-App-fr": "default",
            "X-Lang": "zh",
            "X-Request-Id": request_id,
            "X-Exp-Groups": "",
            "X-Device-Model": "",
            "X-Device-Brand": "",
            "X-Timestamp": sign_info["timestamp"],
            "X-Nonce": sign_info["x_nonce"],
            "X-Sign": sign_info["sign"],
        }
        
        assistant_id = self.DEFAULT_ASSISTANT_ID
        if request.model and len(request.model) == 24 and re.match(r'^[0-9a-f]{24}$', request.model):
            assistant_id = request.model
        
        payload = {
            "assistant_id": assistant_id,
            "conversation_id": "",
            "project_id": "",
            "chat_type": "user_chat",
            "messages": self._prepare_messages(request.messages),
            "meta_data": {
                "cogview": {"rm_label_watermark": False},
                "is_test": False,
                "input_question_type": "xxxx",
                "channel": "",
                "draft_id": "",
                "chat_mode": "zero",
                "is_networking": False,
                "quote_log_id": "",
                "platform": "pc"
            }
        }
        
        conversation_id = ""
        full_content = ""
        last_logic_id = None
        
        async with client.stream(
            "POST",
            f"{self.base_url}/chatglm/backend-api/assistant/stream",
            headers=headers,
            json=payload
        ) as response:
            response.raise_for_status()
            
            async for line in response.aiter_lines():
                event = self._parse_stream_response(line)
                if not event:
                    continue
                
                if event.get("conversation_id"):
                    conversation_id = event.get("conversation_id")
                
                parts = event.get("parts", [])
                for part in parts:
                    if part.get("role") == "assistant":
                        logic_id = part.get("logic_id")
                        part_content = part.get("content", [])
                        for item in part_content:
                            if item.get("type") == "text":
                                text = item.get("text", "")
                                if logic_id != last_logic_id:
                                    full_content = text
                                    last_logic_id = logic_id
                                elif text and len(text) > len(full_content):
                                    full_content = text
        
        if conversation_id:
            await self._delete_conversation(conversation_id, access_token, assistant_id)
        
        return ChatCompletionResponse(
            id=conversation_id or generate_uuid(),
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
        
        if not self.token:
            raise ValueError("Zhipu token (refresh_token) is required")
        
        access_token = await self._get_access_token(self.token)
        
        token_payload = decode_jwt_payload(access_token)
        device_id = token_payload.get("device_id") if token_payload else generate_uuid_no_dash()
        
        sign_info = generate_sign()
        request_id = generate_uuid_no_dash()
        
        headers = {
            "Accept": "text/event-stream",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "App-Name": "chatglm",
            "Origin": "https://chatglm.cn",
            "Referer": "https://chatglm.cn/main/alltoolsdetail",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "X-Device-Id": device_id,
            "X-App-Platform": "pc",
            "X-App-Version": "0.0.1",
            "X-App-fr": "default",
            "X-Lang": "zh",
            "X-Request-Id": request_id,
            "X-Exp-Groups": "",
            "X-Device-Model": "",
            "X-Device-Brand": "",
            "X-Timestamp": sign_info["timestamp"],
            "X-Nonce": sign_info["x_nonce"],
            "X-Sign": sign_info["sign"],
        }
        
        assistant_id = self.DEFAULT_ASSISTANT_ID
        if request.model and len(request.model) == 24 and re.match(r'^[0-9a-f]{24}$', request.model):
            assistant_id = request.model
        
        payload = {
            "assistant_id": assistant_id,
            "conversation_id": "",
            "project_id": "",
            "chat_type": "user_chat",
            "messages": self._prepare_messages(request.messages),
            "meta_data": {
                "cogview": {"rm_label_watermark": False},
                "is_test": False,
                "input_question_type": "xxxx",
                "channel": "",
                "draft_id": "",
                "chat_mode": "zero",
                "is_networking": False,
                "quote_log_id": "",
                "platform": "pc"
            }
        }
        
        conversation_id = ""
        chunk_id = generate_uuid()
        created = int(time.time())
        
        async with client.stream(
            "POST",
            f"{self.base_url}/chatglm/backend-api/assistant/stream",
            headers=headers,
            json=payload
        ) as response:
            response.raise_for_status()
            
            last_content = ""
            last_think_content = ""
            
            async for line in response.aiter_lines():
                event = self._parse_stream_response(line)
                if not event:
                    continue
                
                if event.get("conversation_id"):
                    conversation_id = event.get("conversation_id")
                
                parts = event.get("parts", [])
                for part in parts:
                    if part.get("role") == "assistant":
                        part_content = part.get("content", [])
                        for item in part_content:
                            if item.get("type") == "text":
                                content = item.get("text", "")
                                if content and content != last_content:
                                    if content.startswith(last_content):
                                        delta = content[len(last_content):]
                                        if delta:
                                            yield ChatCompletionChunk(
                                                id=conversation_id or chunk_id,
                                                created=created,
                                                model=request.model,
                                                choices=[
                                                    StreamChoice(
                                                        index=0,
                                                        delta=DeltaMessage(content=delta),
                                                        finish_reason=None
                                                    )
                                                ]
                                            )
                                    else:
                                        yield ChatCompletionChunk(
                                            id=conversation_id or chunk_id,
                                            created=created,
                                            model=request.model,
                                            choices=[
                                                StreamChoice(
                                                    index=0,
                                                    delta=DeltaMessage(content=content),
                                                    finish_reason=None
                                                )
                                            ]
                                        )
                                    last_content = content
                            elif item.get("type") == "think":
                                think_content = item.get("text", "")
                                if think_content and think_content != last_think_content:
                                    if think_content.startswith(last_think_content):
                                        delta = think_content[len(last_think_content):]
                                        if delta:
                                            yield ChatCompletionChunk(
                                                id=conversation_id or chunk_id,
                                                created=created,
                                                model=request.model,
                                                choices=[
                                                    StreamChoice(
                                                        index=0,
                                                        delta=DeltaMessage(content=f"<think:{delta}>"),
                                                        finish_reason=None
                                                    )
                                                ]
                                            )
                                    else:
                                        yield ChatCompletionChunk(
                                            id=conversation_id or chunk_id,
                                            created=created,
                                            model=request.model,
                                            choices=[
                                                StreamChoice(
                                                    index=0,
                                                    delta=DeltaMessage(content=f"<think:{think_content}>"),
                                                    finish_reason=None
                                                )
                                            ]
                                        )
                                    last_think_content = think_content
        
        yield ChatCompletionChunk(
            id=conversation_id or chunk_id,
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
        
        if conversation_id:
            await self._delete_conversation(conversation_id, access_token, assistant_id)
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
