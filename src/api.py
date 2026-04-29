import json
import time
import uuid
from typing import Optional, List, Dict, Any, AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .config import settings
from .models import (
    ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChunk,
    ChatMessage, ChatCompletionChoice, StreamChoice, DeltaMessage, Usage,
    ModelList, ModelInfo
)
from .providers import DeepSeekProvider, KimiProvider, MetasoProvider, DoubaoProvider, QwenProvider, ZhipuProvider, MiniMaxProvider, BaseProvider


providers: Dict[str, BaseProvider] = {}


def get_provider_for_model(model: str) -> tuple[str, BaseProvider]:
    model_lower = model.lower()
    
    if any(x in model_lower for x in ["deepseek", "ds-"]):
        provider_name = "deepseek"
        if provider_name not in providers:
            token = settings.providers.deepseek.token
            providers[provider_name] = DeepSeekProvider(token=token)
        return provider_name, providers[provider_name]
    
    if any(x in model_lower for x in ["kimi", "moonshot"]):
        provider_name = "kimi"
        if provider_name not in providers:
            token = settings.providers.kimi.token
            providers[provider_name] = KimiProvider(token=token)
        return provider_name, providers[provider_name]
    
    if any(x in model_lower for x in ["metaso"]):
        provider_name = "metaso"
        if provider_name not in providers:
            token = settings.providers.metaso.token
            providers[provider_name] = MetasoProvider(token=token)
        return provider_name, providers[provider_name]
    
    if any(x in model_lower for x in ["doubao"]):
        provider_name = "doubao"
        if provider_name not in providers:
            token = settings.providers.doubao.token
            providers[provider_name] = DoubaoProvider(token=token)
        return provider_name, providers[provider_name]
    
    if any(x in model_lower for x in ["qwen", "tongyi"]):
        provider_name = "qwen"
        if provider_name not in providers:
            token = settings.providers.qwen.token
            providers[provider_name] = QwenProvider(token=token)
        return provider_name, providers[provider_name]
    
    if any(x in model_lower for x in ["zhipu", "chatglm", "glm"]):
        provider_name = "zhipu"
        if provider_name not in providers:
            token = settings.providers.zhipu.token
            providers[provider_name] = ZhipuProvider(token=token)
        return provider_name, providers[provider_name]
    
    if any(x in model_lower for x in ["minimax"]):
        provider_name = "minimax"
        if provider_name not in providers:
            token = settings.providers.minimax.token
            providers[provider_name] = MiniMaxProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.deepseek.token:
        provider_name = "deepseek"
        if provider_name not in providers:
            token = settings.providers.deepseek.token
            providers[provider_name] = DeepSeekProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.kimi.token:
        provider_name = "kimi"
        if provider_name not in providers:
            token = settings.providers.kimi.token
            providers[provider_name] = KimiProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.metaso.token:
        provider_name = "metaso"
        if provider_name not in providers:
            token = settings.providers.metaso.token
            providers[provider_name] = MetasoProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.doubao.token:
        provider_name = "doubao"
        if provider_name not in providers:
            token = settings.providers.doubao.token
            providers[provider_name] = DoubaoProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.qwen.token:
        provider_name = "qwen"
        if provider_name not in providers:
            token = settings.providers.qwen.token
            providers[provider_name] = QwenProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.zhipu.token:
        provider_name = "zhipu"
        if provider_name not in providers:
            token = settings.providers.zhipu.token
            providers[provider_name] = ZhipuProvider(token=token)
        return provider_name, providers[provider_name]
    
    if settings.providers.minimax.token:
        provider_name = "minimax"
        if provider_name not in providers:
            token = settings.providers.minimax.token
            providers[provider_name] = MiniMaxProvider(token=token)
        return provider_name, providers[provider_name]
    
    raise HTTPException(status_code=400, detail=f"No provider available for model: {model}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for provider in providers.values():
        await provider.close()


app = FastAPI(
    title="NXAPI - OpenAI Compatible API",
    description="大模型 API 中转站，支持 DeepSeek、Kimi、Metaso、豆包、千问、智谱清言和 MiniMax",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "NXAPI - OpenAI Compatible API", "version": "1.0.0"}


@app.get("/v1/models", response_model=ModelList)
async def list_models():
    models = []
    
    if settings.providers.deepseek.token:
        provider = DeepSeekProvider(token=settings.providers.deepseek.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="deepseek"))
    
    if settings.providers.kimi.token:
        provider = KimiProvider(token=settings.providers.kimi.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="moonshot"))
    
    if settings.providers.metaso.token:
        provider = MetasoProvider(token=settings.providers.metaso.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="metaso"))
    
    if settings.providers.doubao.token:
        provider = DoubaoProvider(token=settings.providers.doubao.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="doubao"))
    
    if settings.providers.qwen.token:
        provider = QwenProvider(token=settings.providers.qwen.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="qwen"))
    
    if settings.providers.zhipu.token:
        provider = ZhipuProvider(token=settings.providers.zhipu.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="zhipu"))
    
    if settings.providers.minimax.token:
        provider = MiniMaxProvider(token=settings.providers.minimax.token)
        for model_id in provider.models:
            models.append(ModelInfo(id=model_id, owned_by="minimax"))
    
    return ModelList(data=models)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(None)
):
    try:
        provider_name, provider = get_provider_for_model(request.model)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if request.stream:
        return StreamingResponse(
            stream_chat_completion(provider, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    else:
        try:
            response = await provider.chat_completion(request)
            return response
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


async def stream_chat_completion(
    provider: BaseProvider,
    request: ChatCompletionRequest
) -> AsyncGenerator[str, None]:
    try:
        async for chunk in provider.chat_completion_stream(request):
            data = chunk.model_dump_json(exclude_unset=True, exclude_none=True)
            yield f"data: {data}\n\n"
        
        yield "data: [DONE]\n\n"
    except Exception as e:
        error_data = json.dumps({"error": {"message": str(e), "type": "internal_error"}})
        yield f"data: {error_data}\n\n"


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": exc.detail, "type": "api_error"}}
    )


def run_server():
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port
    )


if __name__ == "__main__":
    run_server()
