from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import yaml
import os
from dotenv import load_dotenv

load_dotenv()


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000


class DeepSeekConfig(BaseSettings):
    base_url: str = "https://chat.deepseek.com"
    enabled: bool = True
    token: Optional[str] = None


class KimiConfig(BaseSettings):
    base_url: str = "https://www.kimi.com"
    enabled: bool = True
    token: Optional[str] = None
    refresh_token: Optional[str] = None


class MetasoConfig(BaseSettings):
    base_url: str = "https://metaso.cn"
    enabled: bool = True
    token: Optional[str] = None


class DoubaoConfig(BaseSettings):
    base_url: str = "https://www.doubao.com"
    enabled: bool = True
    token: Optional[str] = None


class QwenConfig(BaseSettings):
    base_url: str = "https://qianwen.biz.aliyun.com/dialog"
    enabled: bool = True
    token: Optional[str] = None


class ZhipuConfig(BaseSettings):
    base_url: str = "https://chatglm.cn"
    enabled: bool = True
    token: Optional[str] = None


class MiniMaxConfig(BaseSettings):
    base_url: str = "https://agent.minimaxi.com"
    enabled: bool = True
    token: Optional[str] = None


class ProviderConfig(BaseSettings):
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    kimi: KimiConfig = Field(default_factory=KimiConfig)
    metaso: MetasoConfig = Field(default_factory=MetasoConfig)
    doubao: DoubaoConfig = Field(default_factory=DoubaoConfig)
    qwen: QwenConfig = Field(default_factory=QwenConfig)
    zhipu: ZhipuConfig = Field(default_factory=ZhipuConfig)
    minimax: MiniMaxConfig = Field(default_factory=MiniMaxConfig)


class Config(BaseSettings):
    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProviderConfig = Field(default_factory=ProviderConfig)

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        config_data = {}
        
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
        
        deepseek_token = os.getenv("DEEPSEEK_TOKEN")
        kimi_token = os.getenv("KIMI_TOKEN")
        kimi_refresh_token = os.getenv("KIMI_REFRESH_TOKEN")
        metaso_token = os.getenv("METASO_TOKEN")
        doubao_token = os.getenv("DOUBAO_TOKEN")
        qwen_token = os.getenv("QWEN_TOKEN")
        zhipu_token = os.getenv("ZHIPU_TOKEN")
        minimax_token = os.getenv("MINIMAX_TOKEN")
        
        if config_data.get("providers"):
            if deepseek_token:
                config_data["providers"]["deepseek"]["token"] = deepseek_token
            if kimi_token:
                config_data["providers"]["kimi"]["token"] = kimi_token
            if kimi_refresh_token:
                config_data["providers"]["kimi"]["refresh_token"] = kimi_refresh_token
            if metaso_token:
                config_data["providers"]["metaso"]["token"] = metaso_token
            if doubao_token:
                config_data["providers"]["doubao"]["token"] = doubao_token
            if qwen_token:
                config_data["providers"]["qwen"]["token"] = qwen_token
            if zhipu_token:
                config_data["providers"]["zhipu"]["token"] = zhipu_token
            if minimax_token:
                config_data["providers"]["minimax"]["token"] = minimax_token
        
        return cls(**config_data)


config = Config.load()
settings = config
