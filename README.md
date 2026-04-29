# 多模型API中转站

一个统一的 OpenAI 兼容 API 中转服务，通过逆向工程实现对多个国内主流大模型平台的调用。

## 支持的平台

| 平台 | 模型 | 状态 |
|------|------|------|
| DeepSeek | deepseek-chat, deepseek-reasoner, deepseek-v4-flash, deepseek-v4-pro, deepseek-r1 | ✅ |
| Kimi (月之暗面) | kimi, kimi-k2.6, kimi-k2.6-code, kimi-k2.5, kimi-k2, kimi-k1.5 | ✅ |
| Metaso (秘塔AI搜索) | metaso, metaso-fast, metaso-concise, metaso-detail, metaso-research | ✅ |
| 豆包 (字节跳动) | doubao, doubao-pro, doubao-lite, doubao-pro-v1, doubao-lite-4k/32k, doubao-seedream-3 | ✅ |
| 千问 (通义千问) | qwen, qwen3, qwen3.5-plus, qwen3.6-plus, qwen3-max, qwen3-flash, qwen3-coder, qwen-long | ✅ |
| 智谱清言 (ChatGLM) | zhipu, chatglm, glm-4-plus, glm-5, glm-5-plus, glm-5.1, glm-5.1-plus | ✅ |
| MiniMax (海螺AI) | minimax, minimax-auto, MiniMax-M2.5, MiniMax-M2.7 | ✅ |

## 功能特性

- **OpenAI 兼容接口**：完全兼容 OpenAI API 格式，可直接替换现有应用
- **流式响应**：支持 SSE 流式输出
- **多模型支持**：一个服务支持多个大模型平台
- **思维链输出**：支持 DeepSeek R1、GLM 等模型的思维链内容输出

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
1. 访问 [Kimi 官网](https://kimi.moonshot.cn/) 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application 标签页
4. 在左侧找到 Cookies -> https://kimi.moonshot.cn
5. 找到 `kimi_access_token` 或 `access_token` 的值

**Token 格式**：JWT 格式，以 `eyJ` 开头的长字符串

**原理**：Kimi 使用 WebSocket 进行实时通信，Token 用于建立连接时的身份验证。服务实现了 WebSocket 协议的完整逆向，包括消息帧的编解码。

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
├── .env                    # Token 配置
├── requirements.txt        # 依赖列表
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
| Kimi | JWT Token | WebSocket 通信、二进制帧编解码 |
| Metaso | Cookie (uid+sid) | 搜索模式参数 |
| 豆包 | Session Cookie | 设备指纹生成、请求签名 |
| 千问 | Cookie (SSO) | XSRF Token 处理 |
| 智谱 | JWT Token | MD5 签名、Token 自动刷新 |
| MiniMax | JWT Token | LocalStorage Token 认证 |

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

## License

MIT License
