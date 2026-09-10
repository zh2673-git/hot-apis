# 多模型API中转站

一个统一的 OpenAI 兼容 API 中转服务，通过逆向工程实现对多个国内主流大模型平台的调用。

> 当前版本 **v1.4.0**（2026-09-11）· 变更记录见 [模型更新记录](#模型更新记录) · 上游接口变更见 [已知问题](#已知问题2026-09-真机实测)

## 支持的平台

| 平台 | 模型 | 状态 |
|------|------|------|
| DeepSeek | deepseek-flash, deepseek-reasoner, deepseek-chat, deepseek-v4-flash, deepseek-v4-pro, deepseek-r1 | ✅ |
| Kimi (月之暗面) | kimi, kimi-k3, kimi-k2.7-code, kimi-k2.7-code-highspeed, kimi-k2.6 | ✅ 支持自动续期 |
| Metaso (秘塔AI搜索) | metaso, metaso-fast, metaso-concise, metaso-detail, metaso-research, metaso-deep-research, metaso-scholar | ⚠️ 上游改版，暂不可用 |
| 豆包 (字节跳动) | doubao, doubao-seed-2.1-pro, doubao-seed-2.1-turbo, doubao-seed-2.0-pro, doubao-pro, doubao-lite, doubao-seedream-3 | ✅ |
| 千问 (通义千问) | qwen, qwen3, qwen3.8-max, qwen3.7-max, qwen3.6-flash | ⚠️ 上游改版，暂不可用 |
| 智谱清言 (ChatGLM) | zhipu, chatglm, glm-5.3, glm-5.3-flash, glm-5.1, glm-5.1-plus, glm-5, glm-5-plus, glm-4-plus | ✅ |
| MiniMax (海螺AI) | minimax, minimax-auto, MiniMax-M3, MiniMax-M2.7, MiniMax-M2.5 | ✅ |

> 模型清单最后同步于 2026-09-10，全部经真机实测（见 [实测验证](#实测验证)）；各平台完整清单见 `src/providers/*.py` 的 `models` 属性与 `GET /v1/models`。
> 千问与秘塔的**清单已是站点真实值**，但 provider 因上游风控暂时无法调用，详见 [已知问题](#已知问题2026-09-真机实测)。

## 功能特性

- **OpenAI 兼容接口**：完全兼容 OpenAI API 格式，可直接替换现有应用
- **流式响应**：支持 SSE 流式输出
- **多模型支持**：一个服务支持多个大模型平台
- **思维链输出**：支持 DeepSeek R1、GLM 等模型的思维链内容输出
- **Token 自动续期**：Kimi 的 `access_token` 仅约 15 分钟有效，配置 `KIMI_REFRESH_TOKEN` 后自动续期
- **工具调用（Agent 支持）**：支持 OpenAI 标准 `tools` / `tool_calls` / `role:"tool"`，
  可直接作为 Cline / Roo Code / Continue / OpenCode 等 agent 的模型后端（见下方专章）
- **端到端实测脚本**：`verify_models.py` 启动真实 uvicorn 服务，逐模型验证连通性

## 工具调用（Agent 支持）

`/v1/chat/completions` 支持 OpenAI 标准工具调用协议，可直接接入 agent 客户端。

**原理**：上游是 Web 逆向的聊天接口，**不具备原生 function calling**（没有 `tools` 参数可透传）。
因此本项目内置一个**无状态的协议翻译层**（`src/tools/`）：把 `tools` 规范与完整对话历史
渲染进提示词，再把模型输出解析回标准 `tool_calls`。
设计文档见 [docs/01-项目方案.md](docs/01-项目方案.md)、[docs/03-模块设计.md](docs/03-模块设计.md)。

### 用法

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-flash",
    "messages": [{"role": "user", "content": "北京今天天气怎么样？"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "查询指定城市的天气",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]}
      }
    }],
    "stream": false
  }'
```

响应中 `choices[0].message.tool_calls` 为标准结构，`finish_reason` 为 `tool_calls`；
把执行结果以 `{"role": "tool", "tool_call_id": "call_xxx", "content": "..."}` 回传即可继续多轮。

### 支持矩阵（真机实测 2026-09-11）

| 平台 | 工具调用 | 门槛内成功率 | 备注 |
|---|---|---|---|
| DeepSeek | ✅ | 100%（4/4） | 支持一轮多工具调用 |
| Kimi | ✅ | 100%（4/4） | 支持一轮多工具调用 |
| 豆包 | ✅ | 100%（4/4） | 需并行调用时倾向直接自答 |
| 智谱清言 | ⚠️ | 50%（2/4） | provider 间歇性返回空（既有问题），**暂不建议用于 agent** |
| MiniMax | 未测 | — | — |
| 千问 / 秘塔 | ❌ | — | 上游风控 / 限流，provider 当前不可用 |

> 成功率为门槛内用例（U1/U2/U3/U5）统计，门槛 ≥80%，详见
> [docs/test-report-v1.md](docs/test-report-v1.md)。

### 注意事项

- **建议走流式**：上游非流式延迟高（MiniMax 实测 30–40s），agent 场景请用 `stream: true`
- **不要高频压测**：Web 逆向通道会触发上游限流（实测同一平台连续调用约 12 次后开始返回空）
- **`tool_choice`** 支持 `"auto"` / `"none"` / `"required"` / `{"type":"function","function":{"name":...}}`
- **解析容错**：同时识别 `<tool_call>{json}</tool_call>`、```json 围栏、裸 JSON 三种格式；
  解析失败一律**降级为普通文本**，不返回 5xx
- **不传 `tools` 时行为与改造前逐字段一致**（由 `capture_baseline.py` 守护该不变量）

### 验证

```bash
python verify_tools.py                # 离线单测（24 条断言）
python verify_tools.py --llm          # L 维度：真实工具调用用例 U1–U5
python verify_tools.py --soak 100     # I5：无状态泄漏（100 次混合请求）
python capture_baseline.py --compare  # I1：不传 tools 时响应结构等价
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 Token

复制 `.env.example` 为 `.env`，并填入你的 Token：

```bash
cp .env.example .env
```

### 3. 启动服务

```bash
python main.py
```

服务将在 `http://localhost:8000` 启动。

## ⚠️ 重要提示

由于本项目采用 Web 逆向方式获取 API 接口，**Token 基于 Web 会话认证**，而非官方 API Key。因此：

1. **Token 可能会过期**：Web Token 通常有会话时效限制，长时间未使用可能需要重新获取
2. **如遇 401/403 错误**：请重新登录对应平台并获取新的 Token
3. **建议**：定期检查 Token 有效性，或在 Token 过期后重新获取

如遇到认证错误，请参考下方 Token 获取指南重新获取。

## Token 获取指南

### DeepSeek

**获取方式**：
1. 访问 [DeepSeek 官网](https://chat.deepseek.com/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Network 标签页
4. 发送一条消息
5. 找到任意 API 请求，查看请求头中的 `authorization` 字段
6. 复制 `Bearer ` 后面的 Token 值

**Token 格式**：一串 Base64 编码的字符串

**原理**：DeepSeek 使用 Bearer Token 认证，Token 中包含用户会话信息。服务会自动处理 PoW (Proof of Work) 挑战验证。

---

### Kimi (月之暗面)

**获取方式**：
1. 访问 [Kimi 官网](https://www.kimi.com/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Local Storage -> https://www.kimi.com
5. 复制 `access_token` 的值填到 `KIMI_TOKEN`，`refresh_token` 的值填到 `KIMI_REFRESH_TOKEN`

**Token 格式**：JWT 格式，以 `eyJ` 开头的长字符串

**有效期**：`access_token` 仅约 **15 分钟**有效；`refresh_token` 约 **90 天**。
填了 `KIMI_REFRESH_TOKEN` 后，服务会在 access_token 临近过期时自动调用
`https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken` 续期，无需手动更换。

**原理**：Kimi 使用 Connect 协议（HTTP 上的二进制帧）通信，Token 用于身份验证。服务实现了消息帧的完整逆向，包括编解码。

---

### Metaso (秘塔AI搜索)

**获取方式**：
1. 访问 [Metaso 官网](https://metaso.cn/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Cookies -> https://metaso.cn
5. 找到 `uid` 和 `sid` 两个 Cookie 的值
6. 将两者用 `-` 连接：`uid-sid`

**Token 格式**：`uid-sid` 格式，例如：`your_uid_here-your_sid_here`

**原理**：Metaso 使用 uid 和 sid 组合进行用户身份验证，服务会自动构造包含这些信息的 Cookie。

---

### 豆包 (字节跳动)

**获取方式**：
1. 访问 [豆包官网](https://www.doubao.com/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Cookies -> https://www.doubao.com
5. 找到 `s_v_web_id` 或 `sessionid` 的值

**Token 格式**：一串 32 位十六进制字符，例如：`your_32_char_hex_token_here`

**原理**：豆包使用字节跳动内部的会话认证机制，Token 用于标识用户会话。服务实现了完整的请求签名和设备指纹生成。

---

### 千问 (通义千问)

**获取方式**：
1. 访问 [通义千问官网](https://www.qianwen.com/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Cookies -> https://www.qianwen.com
5. 复制完整的 Cookie 字符串（包含 `tongyi_sso_ticket`, `XSRF-TOKEN` 等）

**Token 格式**：完整的 Cookie 字符串，例如：
```
UM_distinctid=xxx; tongyi_sso_ticket=xxx; XSRF-TOKEN=xxx; ...
```

**原理**：千问使用阿里云的 SSO 认证体系，需要完整的 Cookie 来通过身份验证。服务会自动解析 Cookie 中的关键信息。

> ⚠️ **当前不可用**：千问站点已迁至风控网关，纯 HTTP 调用被拒绝（旧端点只回「请升级至最新版」），
> 详见 [已知问题](#已知问题2026-09-真机实测)。模型清单已同步为站点真实值，待浏览器代理方案落地后即可使用。

---

### 智谱清言 (ChatGLM)

**获取方式**：
1. 访问 [智谱清言官网](https://chatglm.cn/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Cookies -> https://chatglm.cn
5. 找到 `chatglm_refresh_token` 的值

**Token 格式**：JWT 格式，以 `eyJ` 开头的长字符串

**原理**：智谱清言使用 JWT Token 进行认证，分为 access_token 和 refresh_token。服务使用 refresh_token 自动获取 access_token，并实现了请求签名算法（MD5）。

**签名算法**：
```python
timestamp = generate_timestamp()  # 特殊格式的时间戳
x_nonce = uuid.uuid4().hex        # 随机 nonce
secret = "8a1317a7468aa3ad86e997d08f3f31cb"  # 固定密钥
sign = md5(f"{timestamp}-{x_nonce}-{secret}")
```

---

### MiniMax (海螺AI)

**获取方式**：
1. 访问 [MiniMax Agent官网](https://agent.minimaxi.com/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Local Storage -> https://agent.minimaxi.com
5. 找到 `_token` 的值

**Token 格式**：JWT 格式，以 `eyJ` 开头的长字符串

**原理**：MiniMax Agent 使用 JWT Token 进行认证，Token 存储在 LocalStorage 中。服务实现了完整的请求签名算法（MD5），包括：
- `x-signature`: MD5(timestamp + secret + body)
- `yy`: MD5(encoded_path + "_" + body + md5(time_ms) + "ooui")

**签名算法**：
```python
# x-signature 生成
signature = md5(f"{timestamp}I*7Cf%WZ#S&%1RlZJ&C2{body}")

# yy 生成  
yy = md5(f"{encoded_path}_{body}{md5(str(time_ms))}ooui")
```

**支持模型**：
- `minimax` / `minimax-auto` - Auto 模式
- `MiniMax-M3` - MiniMax M3 旗舰模型（100 万上下文、原生多模态）
- `MiniMax-M2.7` - MiniMax M2.7 对话模型
- `MiniMax-M2.5` - MiniMax M2.5 对话模型

**注意**：MiniMax Agent 平台与 MiniMax 开放 API 是不同的服务，模型名称也不同。

---

## API 使用示例

### 列出可用模型

```bash
curl http://localhost:8000/v1/models
```

### 对话补全 (非流式)

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

### 对话补全 (流式)

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### Python 示例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed"
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "你好"}],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

## 思维链输出

对于支持思维链的模型（如 DeepSeek R1、GLM），思维链内容会以特殊格式输出：

- DeepSeek R1：思维链内容以 `<think:内容>` 格式输出
- GLM：思维链内容以 `<think:内容>` 格式输出

示例输出：
```
<think:让我思考一下这个问题...>好的，我来回答你的问题...
```

## 项目结构

```
nxapi/
├── src/
│   ├── api.py              # API 路由定义
│   ├── main.py             # 应用入口
│   ├── config/
│   │   └── settings.py     # 配置管理
│   ├── models/
│   │   └── schemas.py      # 数据模型
│   └── providers/
│       ├── base.py         # Provider 基类
│       ├── deepseek.py     # DeepSeek 实现
│       ├── kimi.py         # Kimi 实现
│       ├── metaso.py       # Metaso 实现
│       ├── doubao.py       # 豆包 实现
│       ├── qwen.py         # 千问 实现
│       ├── zhipu.py        # 智谱 实现
│       └── minimax.py      # MiniMax 实现
├── config.yaml             # 服务配置
├── .env.example            # Token 配置模板（复制为 .env 使用）
├── requirements.txt        # 依赖列表
├── verify_models.py        # 端到端实测脚本
└── main.py                 # 启动脚本
```

## 技术原理

### 逆向工程方法

本项目通过以下方式进行 API 逆向：

1. **网络抓包分析**：使用浏览器开发者工具捕获 API 请求
2. **请求参数分析**：分析请求头、请求体、认证方式
3. **签名算法还原**：逆向分析签名/加密算法并用 Python 实现
4. **协议模拟**：模拟完整的 HTTP/WebSocket 通信流程

### 各平台技术要点

| 平台 | 认证方式 | 特殊处理 |
|------|----------|----------|
| DeepSeek | Bearer Token | PoW 挑战验证、SHA3 哈希计算 |
| Kimi | JWT Token | Connect 协议二进制帧编解码、**access_token 自动续期**（auth.kimi.com） |
| Metaso | Cookie (uid+sid) | ⚠️ 搜索端点已改为 `POST /api/search/chat`，非浏览器客户端被限流 |
| 豆包 | Session Cookie | 设备指纹生成、请求签名 |
| 千问 | Cookie (SSO) | ⚠️ 站点迁至 `www.qianwen.com` 风控网关，需浏览器 SDK 生成的签名头 |
| 智谱 | JWT Token | MD5 签名、Token 自动刷新 |
| MiniMax | JWT Token | Query 参数认证（`token`/`device_id`）+ MD5 签名（`x-signature` / `yy`） |

### 安全说明

- 所有 Token 仅存储在本地 `.env` 文件中
- 不会向任何第三方发送 Token
- 建议定期更新 Token 以确保安全

## 注意事项

1. **仅供学习研究**：本项目仅用于技术研究和学习，请勿用于商业用途
2. **API 稳定性**：由于是逆向实现，官方 API 变更可能导致服务不可用
3. **使用限制**：请遵守各平台的使用条款和频率限制
4. **Token 有效期**：各平台 Token 有不同有效期，过期需重新获取

## 常见问题

### Q: Token 过期了怎么办？
A: 重新按照上述方法获取新的 Token 并更新 `.env` 文件。

### Q: 为什么有些模型响应很慢？
A: 部分模型（如 DeepSeek R1）会输出思维链内容，响应时间较长是正常的。

### Q: 如何获取思维链内容？
A: 思维链内容会包含在响应中，以 `<think:...>` 格式标记。

## 模型更新记录

### v1.3.0（2026-09-10）

各平台同步至当期最新模型，并完成真机实测：

| 平台 | 本次变更 | 实测 |
|------|----------|------|
| DeepSeek | 新增 `deepseek-flash`（对应 V4.1 Flash，2026-09-10 发布，全面接替 V4 Pro）；`deepseek-v4-pro` 官方计划于 2026-09-14 12:00 下线，届时请求自动转由 V4.1 Flash 处理 | ✅ 1/1 |
| Kimi | 新增 `kimi-k3`（2.8T 旗舰）、`kimi-k2.7-code`、`kimi-k2.7-code-highspeed`；移除已下线的 `kimi-k2.5`、`kimi-k2`、`kimi-k1.5` 与 `moonshot-v1` 系列（2026-08-31 起调用返回 404）；**新增 access_token 自动续期** | ✅ 3/3 |
| Metaso | 新增 `metaso-deep-research` 别名（对应「深度研究」模式） | ⚠️ 上游改版 |
| 豆包 | 新增 `doubao-seed-2.1-pro`、`doubao-seed-2.1-turbo`（Seed 2.1 系列，2026-06-23 发布）与 `doubao-seed-2.0-pro` | ✅ 3/3 |
| 千问 | 清单改为站点真实值：`qwen3.8-max`（站点 newTag 最新）、`qwen3.7-max`、`qwen3.6-flash`、`qwen` | ⚠️ 上游改版 |
| 智谱清言 | 新增 `glm-5.3`（旗舰，1M 上下文、思考常开）、`glm-5.3-flash`（原生多模态，2026-08-26 开源） | ✅ 2/2 |
| MiniMax | 新增 `MiniMax-M3`（2026-06-01 发布，1M 上下文、原生多模态） | ✅ 1/1 |

**同时修复的两个既有缺陷**：

1. **MiniMax 全模型 401**：`device_id` 误取自 JWT 的 `user.deviceID`（实际为空），而 Web 端用的是
   客户端生成的 8 位数字 id（缓存在 `tab_device_id`）。抓包确认 `x-signature` 算法与密钥仍有效
   （校验 4/4 匹配）、`token` 取值一致，差异仅在 `device_id`。修复后 `minimax` / `minimax-auto` /
   `MiniMax-M3` / `MiniMax-M2.5` / `MiniMax-M2.7` 全部恢复。
2. **Kimi 每 15 分钟失效**：`access_token` 短时有效，现支持用 `refresh_token` 调用
   `https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken` 自动续期
   （新增 `KIMI_REFRESH_TOKEN` 环境变量）。

> **实测覆盖**：DeepSeek 1/1、Kimi 3/3、豆包 3/3、智谱 2/2、MiniMax 1/1（另含 4 个历史模型回归）。
> 千问与秘塔的失败经历史模型对照实验确认为**上游接口变更**，与本轮清单更新无关，详见
> [已知问题](#已知问题2026-09-真机实测)。

## 已知问题（2026-09 真机实测）

以下问题均通过真实抓包定位，**与本轮模型清单更新无关**，属上游接口变更。

### 千问：站点已迁至风控网关，纯 HTTP 无法调用

- 旧端点 `https://qianwen.biz.aliyun.com/dialog/conversation` 仍会响应，但只返回
  「你正在使用较早版本，为了获得更好的对话体验，请升级至最新版。」（**所有模型**，含历史模型）
- 站点已改为 `https://www.qianwen.com` 下的 `/api/v1/*` 与 `/api/v2/chat`，请求必须携带
  `bx-ua`、`bx-umidtoken`、`clt-acs-sign`、`clt-acs-request-params`、`eo-clt-*`、`x-wpk-*`
  等**由浏览器风控 SDK 在 JS 中生成的签名头**
- 实测：纯 HTTP 直连 `/api/v1/model/list` 与 `/api/v2/chat` 均返回 **404**（网关拒绝路由）
- **结论**：当前「纯 HTTP 客户端」架构下无法修复，需要浏览器代理（Playwright）方案

### 秘塔：搜索端点已改版，但非浏览器客户端被限流

- 搜索端点由 `GET /api/searchV2` 改为 **`POST /api/search/chat`**（OpenAI 风格请求体）：
  ```json
  {"model":"fast_thinking","stream":true,"mode":"detail",
   "messages":[{"id":"temp-…","key":"temp-…","conversationId":"temp-…","role":"user",
                "content":"…","markdownContent":"…","engineType":"quanwang"}]}
  ```
  `engineType` 取值（抓自 `/api/metaso-ai-config`）：`quanwang` 全网 / `scholar` 学术 / `pdf` / `podcast` 播客
- 实测：纯 HTTP 调用返回 `200 text/event-stream`，先收到 `conversation_init` / `user_message_init` /
  `response_message_init`，随后帧为 `{"msg":"Too Many Requests","code":429}`；浏览器会话可正常使用，
  非浏览器客户端被限流
- **结论**：协议已完全定位，但同样需要浏览器代理方案才能稳定调用

### 其他

- 豆包、智谱的 `models` 列表仅用于 `/v1/models` 展示，实际调用由平台侧 `bot_id` / `assistant_id` 决定
- DeepSeek 的 Token 为 64 位非 JWT 串；因 DeepSeek 网页端允许匿名对话，实测通过**不能**排除匿名会话

### 智谱清言：provider 间歇性返回空

- 现象：连**非 tools 路径**下 `glm-*` 全部返回空内容（0.1~0.6s 快速返回），16/16 模型复现
- 对照实验：短问题 / 长 prompt、带 / 不带 `tools` 均复现 → **与工具调用层无关**
- `ZHIPU_TOKEN`（refresh 型）有效期正常（至 2027-03），疑为其 access_token 获取流程失效

### Web 逆向通道抗压能力有限（高频调用触发上游限流）

- 实测：对同一平台连续调用约 **12 次**后开始返回空，且 tools 与非 tools 路径**同时**失效
- 纯 provider 对照（不经 relay、不经工具层）同样 `0/20` → 属**上游限流 / 配额**，非本项目缺陷
- 影响：agent 长会话或高频调用场景建议降低并发；根治方案是改用官方 API（见文末演进路径）

## 实测验证

`verify_models.py` 会启动真实 uvicorn 服务并逐模型发起真实请求：

```bash
python verify_models.py                 # 本轮新增/变更的模型
python verify_models.py --all           # 各平台 models 列表全量
python verify_models.py --models kimi-k3 qwen3.7-max   # 指定模型（对照实验）
python verify_models.py --stream        # 追加流式接口验证
```

## License

MIT License
