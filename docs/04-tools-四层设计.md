# `tools` 模块 · 递归四层设计（含生命周期钩子审计）

> 依据 `project-development-prompt.md` §1.2「递归四层模型」与 §2.3「模块递归四层设计（强制带生命周期钩子）」。
> 递归层级：第0级（项目）→ 第1级（`tools`）→ 第2级（`render` / `parse` / `pipeline`）→ 终止。
> 生成时间：2026-09-11

---

## 0. 父本质锚定（第0级 → 第1级）

**第0级父本质**：NXAPI 工具调用适配层 = 一个**无状态的协议翻译层**，把「只能收自然语言、
只能回自然语言」的聊天模型包装成「能收 `tools` 规范、能回 `tool_calls`」的 OpenAI 兼容端点。

**父时空契约**（子模块必须继承）：

| 维度 | 父契约 | 对子模块的硬约束 |
|---|---|---|
| 空间 | 工具调用多轮状态**不驻留 relay** | 任何子模块**不得**有跨请求可变状态 |
| 时间 | 顺序管道 + 局部状态机 | 子模块不得引入并发；状态机只允许出现在 `parse` |
| 规则 | 运行时拦截 + 失败降级 | 子模块的失败路径必须"降级"而非"抛出" |

---

## 1. 第1级：`tools` 模块

### 1.1 本质（锚定父本质 + 时空契约）

> 在「无状态协议翻译层」的时空约束下，`tools` 模块的本质是
> **一个纯函数集合，负责 `tools+历史 → 文本` 与 `文本 → tool_calls` 的双向映射，全程无副作用。**

三个推论直接来自父契约：

1. 双向映射 → 必然分裂为「正向（render）」与「逆向（parse）」两个子模块（空间职责分离）。
2. 无副作用 → 所有状态必须由调用方传入传出，禁止模块级可变变量（规则防穿透）。
3. 失败降级 → 逆向映射（不可信输入侧）必须自带降级路径（规则契约）。

### 1.2 四层

| 四层 | 内容 | 落点 |
|---|---|---|
| **数据规范** | 定界符常量 `TOOL_CALL_OPEN/CLOSE`；状态枚举 `ParseState`；事件枚举与 `Event` 结构；提示词模板 `SYSTEM_TEMPLATE` / `HISTORY_TEMPLATE` / `FORCE_*_HINT`；工具规范化三元组 | `src/tools/spec.py` |
| **数据存储** | **无**。显式声明：不写文件、不连库、不设缓存。**这是本设计的核心不变量**——没有存储面就没有空间泄漏面 | — |
| **数据流转** | 正向：规范化 → 模板渲染 → 字符串；逆向：字符流 → 状态机 → 结构化事件 → OpenAI 结构 | `render.py` / `parse.py` |
| **数据接口** | 对外只暴露 3 个入口：`pipeline.prepare()`、`pipeline.build_response()`、`pipeline.stream()` | `pipeline.py` |

### 1.3 生命周期钩子（显式结论：**四个均不适用**）

> §2.3 要求"每个核心模块必须显式定义 init/start/stop/destroy"。
> 本模块的结论是**四个钩子均不适用**，理由如下——这**不是遗漏，而是本设计的正确形态**：
> 任何为"对称"而补写的 `init` 都会引入跨请求状态，反而违反空间契约。

| 钩子 | 是否适用 | 理由 | 若无理由硬写会怎样 |
|---|---|---|---|
| `init(ctx)` | ❌ | 无需开辟空间：无缓存、无连接池、无全局表、无注册动作。所有输入由请求携带 | 会引入跨请求状态 → 违反空间契约（3.1） |
| `start()` | ❌ | 无独立执行流。驱动源是 HTTP 请求；宿主运行时（ASGI 事件循环）已由 `src/api.py` 提供 | 会引入后台任务/线程 → 违反时间契约（3.2） |
| `stop()` | ❌ | 没有持续运行的时间流可暂停 | 会引入"半停止"状态 → 增加不可测分支 |
| `destroy()` | ❌ | 请求级对象随请求作用域由 CPython 引用计数回收；模块级无可释放资源 | 会掩盖真实的生命周期归属 |

### 1.4 资源回收路径（`destroy` 的等价物）

本模块**唯一**的"需要显式回收"的资源在**宿主**而非本模块：

| 资源 | 归属 | 回收方式 | 本次是否改动 |
|---|---|---|---|
| 上游 `httpx.AsyncClient`（7 个 provider 各一） | `src/providers`（infrastructure） | 既有 `src/api.py` 的 `lifespan` shutdown 逐 provider `await provider.close()` | **不改**（既有实现已正确） |
| 请求级 `Prepared` / `buffer` / `Event` | 请求作用域 | 请求返回即失去引用 → 引用计数回收 | 新增，天然正确 |
| `ToolCallStreamParser` 的 `buffer` | 生成作用域 | `pipeline.stream()` 的 `finally` 显式清空（防流式中断残留） | 新增 |

**回收顺序**：请求级对象先于连接级对象消亡，无环依赖 → **无需依赖逆序释放**。

---

## 2. 第2级：`render` 子模块

### 2.1 子本质（锚定第1级父本质）

> 在「纯函数集合，负责双向映射且全程无副作用」的时空约束下，
> 本子模块的本质是 **正向映射器：把结构化规则（JSON Schema 工具规范 + 角色化对话历史）
> 降维成单一自然语言文本，以适配"只认最后一条 user 消息"的上游空间约束。**

**为什么是"降维"**：上游的空间约束是硬性的——`kimi`/`doubao`/`qwen` 只取最后一条 `user`
消息（已实测确认）。结构化信息（工具 Schema、多角色历史）必须**编码进一条文本**才能穿过这道窄门。

### 2.2 四层

| 四层 | 内容 |
|---|---|
| 数据规范 | 模板常量（`SYSTEM_TEMPLATE` / `HISTORY_TEMPLATE`）；`tool_choice` 到渲染行为的映射表；`FORCE_*_HINT` 文案 |
| 数据存储 | **无**（中间字符串随请求回收） |
| 数据流转 | 顺序管道：取 system → 取 tools 规范 → 逐条角色标签化 → 拼装 → 追加 current_user → 返回长度恒为 1 的列表。**无分支跳转、无循环回跳** |
| 数据接口 | `build_system_prompt(tools, tool_choice) -> str`；`render_messages(messages, tools, tool_choice) -> List[ChatMessage]` |

### 2.3 钩子

| 钩子 | 适用 | 理由 |
|---|---|---|
| `init` / `start` / `stop` / `destroy` | ❌ 全部 | 无状态纯函数；无资源持有 |

### 2.4 本子模块的断言点

- **A-R1**：`render_messages` 返回值长度**恒为 1**（上游窄门约束）。
- **A-R2**：输出中**不含** `None`（`content is None` 已归一化为 `""`）。
- **A-R3**：`tool_choice == "none"` 时输出**不含** `<tools>` 段。

---

## 3. 第2级：`parse` 子模块

### 3.1 子本质（锚定第1级父本质）

> 在「纯函数集合，负责双向映射且全程无副作用」的时空约束下，
> 本子模块的本质是 **逆向映射器：把不可信的自然语言字符流还原成结构化的 `tool_calls`，
> 并且是 `tools` 模块内唯一持有时间状态的地方（跨 chunk 的解析状态机）。**

**为什么唯一**：父契约规定"时间形态 = 顺序管道 + 局部状态机"。整条链路里唯一
**必须**跨时间片记忆的场景，就是"JSON 被 TCP 分片切成两半"。因此状态机被**收敛在这一个类里**，
其余全部保持无状态。

### 3.2 四层

| 四层 | 内容 |
|---|---|
| 数据规范 | `ParseState`（`NORMAL` / `IN_TOOL_CALL`）；`EventKind`（`CONTENT` / `TOOL_CALL`）；三种可接受格式的优先级表（F1 标签 / F2 围栏 / F3 裸 JSON） |
| 数据存储 | **生成级私有**：`self._buffer: str`、`self._state: ParseState`、`self._passthrough: bool`、`self._calls: list`。生命周期严格等于一次生成 |
| 数据流转 | **状态机跳转**：`NORMAL --命中OPEN--> IN_TOOL_CALL --命中CLOSE--> NORMAL`；快路径嗅探决定 `passthrough` 分叉 |
| 数据接口 | `feed(chunk) -> List[Event]`；`finish() -> List[Event]`；`has_tool_calls() -> bool`；模块级 `extract(text) -> (content, raw_calls)`；`to_tool_calls(raw) -> List[ToolCall]` |

### 3.3 钩子

| 钩子 | 适用 | 理由 |
|---|---|---|
| `init` | ❌ | 构造参数为空，无外部依赖注入 |
| `start` / `stop` | ❌ | 无独立执行流；由 `feed()` 驱动 |
| `destroy` | ❌（但**有等价义务**） | 状态在实例上，实例随生成结束被回收。**等价义务**：`pipeline.stream()` 必须在 `finally` 中丢弃 parser 实例，确保流式中断也不残留 `buffer` |

### 3.4 本子模块的断言点

- **A-P1**：跨 chunk 边界的 JSON 能被正确重组（把 `<tool_call>{"a":1}</tool_call>` 按
  任意切点切成 N 片，`feed` 结果与整包一致）。
- **A-P2**：畸形 JSON → 产出 `CONTENT` 原文，**不抛异常**（I2）。
- **A-P3**：缺闭合标签 + `finish()` → 先尝试解析，失败降级为 `CONTENT`。
- **A-P4**：`passthrough` 模式下首 chunk 立即产出 `CONTENT`（零缓冲延迟）。
- **A-P5**：`feed` 全过程 `NORMAL → IN_TOOL_CALL` 状态可逆且可多次循环（一轮多工具）。

---

## 4. 第2级：`pipeline`（application 编排层）

### 4.1 子本质（锚定第1级父本质）

> 在「纯函数集合，负责双向映射且全程无副作用」的时空约束下，
> 本子模块的本质是 **一次工具调用轮的编排者：串联 `render → 上游 → parse`，
> 并作为"请求级状态"的唯一持有边界。**

**层级归属说明**：`pipeline` 在方法论中属 **application（流转-编排）** 而非 domain——
它不实现映射规则，只负责"把规则按正确顺序接上，并管理这一次时间线的生命周期"。
它是 `tools` 包内唯一允许接触 provider（infrastructure）的位置，且**只通过参数注入**。

### 4.2 四层

| 四层 | 内容 |
|---|---|
| 数据规范 | `Prepared` 结构（压平后的请求 + 规范化 tools + tool_choice + system_prompt） |
| 数据存储 | **请求级私有**：`Prepared` 实例；流式下额外持有 `ToolCallStreamParser` 实例 |
| 数据流转 | 顺序管道：`prepare → provider(上游) → build_response` 或 `stream`（内含 parse 状态机） |
| 数据接口 | `prepare(request)`；`build_response(prepared, text)`；`stream(prepared, provider, request)` |

### 4.3 钩子

| 钩子 | 适用 | 理由 |
|---|---|---|
| `init` / `start` / `stop` | ❌ | 无模块级状态；编排由请求驱动 |
| `destroy` | ❌（**有等价义务**） | 等价义务 = `stream()` 的 `finally` 必须：① 丢弃 parser；② 保证 SSE 终止帧与 `[DONE]` 在异常路径也被发出（否则客户端挂起） |

---

## 5. 不变量断言点 → 测试映射

| 断言 | 归属 | 测试落点 |
|---|---|---|
| A-R1 渲染结果长度恒为 1 | render | `verify_tools.py` 单测 |
| A-R2 输出不含 None | render | 同上 |
| A-R3 `tool_choice=none` 无工具段 | render | 同上 |
| A-P1 跨 chunk JSON 重组 | parse | 分片暴力测试（穷举切点） |
| A-P2/P3 畸形输入降级 | parse | 畸形用例表 |
| A-P4 快路径零延迟 | parse | 断言首 chunk 即产出 |
| A-P5 多工具状态可逆 | parse | 双工具用例 |
| **I1** 无 tools 响应逐字段等价 | api | 基线快照 diff == 0 |
| I2 解析失败不 5xx | api/parse | 注入畸形上游输出 |
| I3 旧路径零改动 | 工程 | `git diff` 范围审查 |
| I4 无新依赖 | 工程 | `requirements.txt` 无 diff |
| I5 无状态泄漏 | 工程 | 100 次混合请求一致性 |
| L 工具调用成功率 ≥80% | 模型 | `verify_tools.py` 用例集 U1–U5 |

---

## 6. 递归终止说明

| 层级 | 单元 | 是否继续拆分 |
|---|---|---|
| 第0级 | NXAPI 工具调用适配层 | 拆为 `tools` 模块 |
| 第1级 | `tools` | 拆为 `render` / `parse` / `pipeline` |
| **第2级** | `render` | **终止**：职能单一（模板渲染），内部无独立子域 |
| **第2级** | `parse` | **终止**：仅一个类 + 两个模块级函数；状态机已被"收敛到最小" |
| **第2级** | `pipeline` | **终止**：只做顺序编排，无内部业务规则 |

**终止依据**（§1.2）：再往下拆只能得到"单一函数或纯数据结构"，无独立数据规范与接口，
满足"直到一个模块内部不再需要拆分子模块为止"的终止条件。全程未触及 3 层上限。
