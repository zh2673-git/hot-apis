# 测试报告 v1 · 工具调用适配层（方案 A）

> 依据 `project-development-prompt.md` §5.3 模板。生成时间：2026-09-11
> 被测对象：`src/tools/`（新增）+ `src/models/schemas.py`、`src/api.py`（改动）

---

## 1. 迭代历程总览

| 版本 | 关键发现 | 核心改进 | P | Q | I | L |
|---|---|---|---|---|---|---|
| v1（初版） | ① U5 普遍失败（模型重复调工具）② 纯文本被 10 字符尾窗扣住 ③ U2 用例歧义 ④ 基线比对口径过脆 | 结尾强提醒 + `tool_result` 规则；动态"定界符前缀后缀"尾窗；U2 用例重设计；基线按「帧形状」比对 | ✅ | ✅ | ✅ | ✅ 3/5 平台达标 |

**本轮失败提取的反模式**见 `docs/anti-patterns.md`（6 条）。

---

## 2. 验证结果

### 2.1 前置条件 P（空间是否就绪）

| 编号 | 检查项 | 结果 | 时空说明 |
|---|---|---|---|
| P1 | 依赖完整（含 `wasmtime`） | ✅ | 空间就绪 |
| P2 | `.env` 至少 1 个可用平台 | ✅ 5 个 | — |
| P3 | 改造**前**录制基线快照 | ✅ 5 平台 | 基线必须在改动前落盘，否则失去比对意义（见反模式 AP-1） |
| P4 | 服务可起、`GET /v1/models` 正常 | ✅ 61 个模型 | — |

### 2.2 后置条件 Q（时间流是否正确）

| 编号 | 检查项 | 结果 | 证据 |
|---|---|---|---|
| Q1 | 工具识别：返回的 `tool_calls[].function.name` 属于已声明集合 | ✅ | L 维度 U1/U2 在 deepseek/kimi/doubao 全部通过 |
| Q2 | 多轮闭环：回传 `role:"tool"` 后能基于结果作答 | ✅ | U5 在 deepseek/kimi/doubao 全部通过 |
| Q3 | 流式与非流式语义一致 | ✅ | 流式端到端实测（kimi）：`role` 帧 → `tool_calls` delta → `finish_reason:"tool_calls"` → `[DONE]` |
| Q4 | `arguments` 可被 `json.loads` 解析 | ✅ | U1/U2 断言中显式 `json.loads` |

**流式实测原始帧**（kimi-k3，已消除纯空白前导）：

```
{"id":"chatcmpl-…","choices":[{"index":0,"delta":{"role":"assistant"}}]}
{"id":"chatcmpl-…","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_…","type":"function","function":{…}}]}}]}
{"id":"chatcmpl-…","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}
data: [DONE]
```

### 2.3 不变量 I（规则是否全程守住）

| 编号 | 不变量 | 结果 | 证据 |
|---|---|---|---|
| **I1** | 不传 `tools` 时响应**逐字段等价**改造前 | ✅ 5/5 | `capture_baseline.py --compare`：deepseek/kimi/doubao/zhipu/minimax 全部"等价" |
| I2 | 解析失败降级为 `content`，不得 5xx | ✅ | 单测 A-P2（畸形 JSON）、A-P3（缺闭合标签）；全路径 try/except 降级 |
| I3 | `/v1/models` 与 7 个 provider 的 chat 路径零改动 | ✅ | `git diff --name-only -- src/providers/` 为空 |
| I4 | 无新增运行时依赖 | ✅ | `requirements.txt` 无 diff |
| I5 | 无请求级状态泄漏（100 次混合请求） | ⚠️ **受阻未完成** | 见 §2.5 |

**离线单测：24 断言全绿**（A-R1/R2/R3、A-P1~P5、F1/F2/F3 三格式、`to_tool_calls` 封装、`prepare` 语义）。

其中 A-P1 用**穷举切点**验证流式解析：把 `<tool_call>{…}</tool_call>` 按任意位置切成两片，
断言聚合结果恒等（这条抓住了"SSE 分片边界不属于契约"这个要点，见反模式 AP-2）。

### 2.4 L 维度（真实请求，门槛 ≥80%）

用例集 U1–U5；U4（并行调用）为 P4 目标，**不计入门槛**，单独记录。

| 平台 | U1 单工具 | U2 多工具选择 | U3 诱导 | U5 结果回传后继续 | **门槛内成功率** | U4 并行 |
|---|---|---|---|---|---|---|
| deepseek (`deepseek-flash`) | ✅ | ✅ | ✅ | ✅ | **100%（4/4）** | ✅ 2 个 |
| kimi (`kimi-k3`) | ✅ | ✅ | ✅ | ✅ | **100%（4/4）** | ✅ 2 个 |
| doubao (`doubao-seed-2.1-pro`) | ✅ | ✅ | ✅ | ✅ | **100%（4/4）** | ❌ 直接自答 |
| zhipu (`glm-5.3-flash`) | ✅ | ✅ | ❌ 空 | ❌ 空 | **50%（2/4）** ❌ | ❌ 空 |

**结论**：3/4 平台达标；zhipu 未达标**且已定位为 provider 层问题**（见 §2.5），非工具层缺陷。

### 2.5 两项"受阻"与它们的对照实验（本轮最重要的方法论产出）

#### ① zhipu 空响应 —— provider 层，与工具层无关

对照实验（**完全不带 `tools`**）：

| 实验 | 结果 |
|---|---|
| 短问题（无 tools），2 次 | 长度均为 0 |
| 长 prompt（无 tools），2 次 | 长度均为 0 |
| `verify_models.py zhipu --all`（非 tools 路径，16 个模型） | **16/16 全部 EMPTY** |

`ZHIPU_TOKEN`（refresh 型）有效期至 2027-03，**未过期** → 其 provider 的 access_token 获取流程失效（既有问题，`src/providers/zhipu.py` 本轮零改动）。

#### ② I5 级联空响应 —— 上游限流，非状态泄漏

I5 跑 100 次交替混合请求，第 12 次起 **tools 与 plain 两条路径同时**返回空；
`plain` 路径是**改造前的原始代码**，因此不可能是工具层的状态泄漏。进一步用
**纯 provider 对照**（不经 relay、不经工具层）复现：

```
纯 provider（无工具层）成功 0/20
```

→ **DeepSeek 上游当前对所有请求返回空**（高频调用触发限流/配额）。
**I5 判定为"环境受阻、未完成"，不是"不变量被破坏"**；待上游恢复后重跑即可。

> 这条实测同时印证了 `docs/02-架构设计.md` §6 的风险项：
> **Web 逆向通道在高频调用下会触发上游限流**，agent 长会话场景需要降并发或改用官方 API。

---

## 3. 时空闭环检查（§5.3 要求）

| 检查项 | 结论 |
|---|---|
| 所有 `init` 是否都有对应的 `destroy`？ | **本层无 `init`**（纯函数集合，显式声明四钩子均不适用，见 `docs/04` §1.3）。唯一需要显式回收的资源是上游 `httpx.AsyncClient`，由**既有** `src/api.py` 的 `lifespan` shutdown 调用 `provider.close()`，本轮未改动 |
| 主循环是否存在无法退出的死循环（时间死锁）？ | 无。解析状态机每轮循环必然满足其一：消费 `TOOL_CALL_CLOSE`/`FENCE` 定界符、`continue` 推进，或 `break`；`IN_TOOL_CALL`/`IN_FENCE` 未闭合时立即 `break` 等待下一 chunk |
| 是否存在跨层直接访问内存的情况（规则穿透）？ | 无。`src/tools/*` **不 import 任何 provider**（`grep -rn "providers" src/tools/` 仅注释），provider 实例由 `api.py` 参数注入；跨层数据一律经 `schemas.py` 类型 |

---

## 4. 反模式库更新

本轮共提取 **6 条**反模式，已写入 `docs/anti-patterns.md`：

| 编号 | 反模式 | 危害 |
|---|---|---|
| AP-1 | 验证工具自身覆盖基线 | 比对必然通过，I1 形同虚设（**本轮差点误判为通过**） |
| AP-2 | 断言比"分片边界"而非"聚合结果" | 把实现细节当契约，产生假失败 |
| AP-3 | 用例题目歧义（可自答/缺必需参数） | 测到的是模型常识，不是被测能力 |
| AP-4 | 把上游空响应当作结构回归 | 假失败，掩盖真实结论 |
| AP-5 | 固定长度尾窗 | 纯文本也被缓冲，违背"快路径零延迟" |
| AP-6 | 把输出格式规则只写在 prompt 开头 | 模型遵循度低，工具调用命中率不足 |
