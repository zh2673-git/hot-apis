# hot-apis · 多模型 API 中转站

一个统一的 **OpenAI 兼容** API 服务：一个地址、一套协议，调用多个国内主流大模型平台。
通过逆向 Web 会话实现，**已支持工具调用（Agent 可直接接入）**。

[English](README_EN.md) | 中文

> **使用范围声明**：本项目仅供**个人学习与技术研究所用**，禁止任何商业用途或对外提供服务。
> 逆向接口不受官方保障，随时可能失效；如需稳定服务，请直接使用各平台**官方 API**（见下文推荐）。

## 功能

- **OpenAI 兼容**：`/v1/chat/completions` + `/v1/models`，用 openai SDK 改个 `base_url` 即可
- **工具调用 ✅**：支持标准 `tools` / `tool_calls` / `role:"tool"`，可直接接入 Cline、Roo Code、
  Continue 等 agent（上游无原生 function calling，由内置协议翻译层模拟实现）
- **流式响应**：SSE 增量输出
- **思维链**：R1 / GLM 等模型的思考过程随响应输出
- **Token 自动续期**：Kimi access_token（约 15 分钟）自动刷新

## 支持平台

| 平台 | 状态 | 代表模型 |
|------|------|----------|
| DeepSeek | ✅ | deepseek-flash, deepseek-reasoner |
| Kimi | ✅ | kimi-k3, kimi-k2.7-code |
| 豆包 | ✅ | doubao-seed-2.1-pro |
| 智谱清言 | ⚠️ 间歇返回空 | glm-5.3 |
| MiniMax | ✅ | MiniMax-M3 |
| 千问 | ❌ 上游风控 | qwen3.8-max |
| 秘塔 | ❌ 上游限流 | metaso-research |

完整模型清单：启动后 `GET /v1/models`，或看 `src/providers/*.py` 的 `models` 属性。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env      # 填入至少一个平台的 Token（获取方法见下表）
python main.py            # 默认 http://localhost:8000（config.yaml 可改）
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="not-needed")
stream = client.chat.completions.create(
    model="deepseek-flash",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

## 工具调用

把 `tools` 按标准 OpenAI 格式传入即可，响应里拿标准 `tool_calls`：

```python
resp = client.chat.completions.create(
    model="deepseek-flash",
    messages=[{"role": "user", "content": "苏州明天天气怎样"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索",
            "parameters": {"type": "object",
                           "properties": {"query": {"type": "string"}},
                           "required": ["query"]},
        },
    }],
)
print(resp.choices[0].finish_reason)   # tool_calls
print(resp.choices[0].message.tool_calls[0].function)
```

工具结果以 `{"role": "tool", "tool_call_id": "...", "content": "..."}` 回传即可多轮循环。

| 平台 | 工具调用 | 备注 |
|---|---|---|
| DeepSeek / Kimi / 豆包 | ✅ 实测 100% | agent 场景建议 `stream: true` |
| 智谱 | ⚠️ | provider 间歇返回空（上游问题），暂不建议用于 agent |
| 千问 / 秘塔 | ❌ | 上游风控/限流，provider 不可用 |

注意：Web 逆向通道**抗不住高频**（同一平台连续约 12 次后开始返回空），agent 请降低并发。

## Token 获取

登录对应平台 → F12 开发者工具 → 按下表取值 → 填进 `.env`（留空的平台即不启用）：

| 环境变量 | 登录地址 | 取值位置 | 格式 |
|---|---|---|---|
| `DEEPSEEK_TOKEN` | chat.deepseek.com | F12 → Network → 请求头 `authorization` 中 `Bearer ` 后的值 | Base64 串 |
| `KIMI_TOKEN` | www.kimi.com | F12 → Application → Local Storage → `access_token` | JWT（`eyJ` 开头） |
| `KIMI_REFRESH_TOKEN` | 同上 | 同上 → `refresh_token`（强烈建议填写，自动续期） | JWT |
| `METASO_TOKEN` | metaso.cn | F12 → Application → Cookies → `uid` 与 `sid` 用 `-` 连接 | `uid-sid` |
| `DOUBAO_TOKEN` | www.doubao.com | F12 → Application → Cookies → `s_v_web_id` 或 `sessionid` | 32 位十六进制 |
| `QWEN_TOKEN` | www.qianwen.com | F12 → Application → Cookies → 复制**完整** Cookie 串 | Cookie 串 |
| `ZHIPU_TOKEN` | chatglm.cn | F12 → Application → Cookies → `chatglm_refresh_token` | JWT |
| `MINIMAX_TOKEN` | agent.minimaxi.com | F12 → Application → Local Storage → `_token` | JWT |

> Token 基于 Web 会话，过期后（401/403）重新按上表获取即可；Kimi 填了 refresh_token 可免维护。

## ⭐ 建议：Token 荒的正解是官方 API

Web 逆向通道天然不稳：Token 会过期、有频率限制、上游一改版就失效。
**如果你的用途超出个人尝鲜，请直接使用官方 API**——全部 OpenAI 兼容，
把上面示例里的 `base_url` 换掉即可，代码一行不用改：

| 平台 | 官方 API Base URL | 控制台 / Key |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | [platform.deepseek.com](https://platform.deepseek.com) |
| Kimi (Moonshot) | `https://api.moonshot.cn/v1` | [platform.moonshot.cn](https://platform.moonshot.cn) |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | [open.bigmodel.cn](https://open.bigmodel.cn) |
| 通义千问（百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | [bailian.console.aliyun.com](https://bailian.console.aliyun.com) |
| 豆包（火山方舟） | `https://ark.cn-beijing.volces.com/api/v3` | [console.volcengine.com/ark](https://console.volcengine.com/ark) |
| MiniMax | `https://api.minimaxi.com/v1` | [platform.minimaxi.com](https://platform.minimaxi.com) |
| 秘塔 | 以官网开放平台为准 | [metaso.cn](https://metaso.cn) |

官方 API 的优势：原生 function calling、稳定不限流、有计费与配额保障、模型命名长期有效。

## 已知局限

- 千问、秘塔：上游迁移至风控网关 / 限流策略，纯 HTTP 架构下暂不可用（详见 [docs](docs/)）
- 智谱：间歇性返回空内容（上游问题，与本项目无关）
- 模型清单来自 Web 端，个别内部编码未经官方文档确认，以实测为准

## 验证脚本

```bash
python verify_models.py          # 模型连通性实测
python verify_models.py --all    # 全量模型
python verify_tools.py           # 工具调用离线单测（49 断言）
python verify_tools.py --llm     # 工具调用真实链路用例
```

## License

MIT License —— 仅限个人研究使用，请遵守各平台服务条款。
