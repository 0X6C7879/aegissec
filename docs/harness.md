# 一、OpenHarness 的 Harness 是怎么设计、怎么实现的

## 1. Agent Loop

### 1.1 设计目标

OpenHarness 对 Agent Loop 的定义非常明确：**模型只负责决定“做什么”，Harness 负责决定“怎么做、何时做、是否允许做、做完如何回填上下文”**。这个思想在 README 的伪代码和实际实现里是一致的。

它的循环核心不是“一次 prompt -> 一次 answer”，而是：

1. 送消息给模型
2. 流式接收文本 / tool_use
3. 如果模型要求调用工具，就执行工具
4. 把工具结果作为新的上下文塞回消息流
5. 再次调用模型
6. 直到模型不再要求工具

这个思想在 `src/openharness/engine/query_engine.py` 的 `QueryEngine.submit_message()` 和 `continue_pending()`，以及 `src/openharness/engine/query.py` 的 `run_query()` 里是落地的。

### 1.2 实现结构

OpenHarness 的 Agent Loop 实现有三层：

第一层是 **Runtime 装配层**。
 `src/openharness/ui/runtime.py::build_runtime()` 会把这些对象拼起来：

- API client
- MCP manager
- Tool registry
- App state
- Hook executor
- Permission checker
- QueryEngine
- System prompt

也就是说，Loop 不是孤立存在的，而是先构建一个 `RuntimeBundle`，再运行。

第二层是 **Engine 状态层**。
 `src/openharness/engine/query_engine.py::QueryEngine` 持有：

- `_messages`
- `_cost_tracker`
- `_api_client`
- `_tool_registry`
- `_permission_checker`
- `_system_prompt`
- `_max_turns`
- `_hook_executor`

这说明 OpenHarness 把“对话历史、成本、工具集、权限器、系统提示词”都作为同一个 Engine 的运行态。

第三层是 **真正的循环层**。
 `src/openharness/engine/query.py::run_query()` 才是主循环：

- 每轮开始先做 `auto_compact_if_needed()`
- 调用 `api_client.stream_message()`
- 流式产出 `AssistantTextDelta`
- 接收 `ApiMessageCompleteEvent`
- 把 assistant message append 进 `messages`
- 如果没有 `tool_uses`，循环结束
- 如果有 tool calls：
  - 单工具顺序执行
  - 多工具 `asyncio.gather` 并发执行
- 工具结果被包装成 `ConversationMessage(role="user", content=tool_results)`
- 回到下一轮

这个点很关键：**工具结果在 OpenHarness 里被建模成一条“用户消息式”的 ToolResultBlock 容器**，这样模型后续看到的是完整的对话轨迹，而不是某个外部 side-channel。

### 1.3 它强在哪

OpenHarness 这个 Loop 强的不是“会调工具”，而是它把以下能力统一进了主循环：

- 流式文本增量
- API retry event
- parallel tool execution
- permission check
- pre / post hook
- token / cost tracking
- pending continuation
- context auto compact

也就是说，它不是单纯的“tool-calling agent”，而是一个 **带治理、带上下文控制、带并行、带恢复能力的 loop kernel**。

------

## 2. Harness Toolkit

### 2.1 设计目标

OpenHarness 的 Toolkit 不是把一堆 Python 函数暴露给模型，而是把工具抽象成一套统一协议：

- 每个工具都有结构化输入
- 每个工具能告诉模型自己的 JSON schema
- 每个工具有只读 / 非只读语义
- 每个工具都走统一的权限检查和 Hook 生命周期

这个设计核心在：

- `src/openharness/tools/base.py`
- `src/openharness/tools/__init__.py`

### 2.2 实现方式

工具抽象非常干净：

`BaseTool` 定义了：

- `name`
- `description`
- `input_model`
- `execute()`
- `is_read_only()`
- `to_api_schema()`

其中 `input_model` 是 Pydantic model。
 这意味着：

1. 输入校验天然统一
2. 可以自动生成 schema 给模型
3. 可以在权限器里基于字段做规则判断

`ToolRegistry` 则只是工具名到工具实例的映射，但很关键的一点是它有 `to_api_schema()`，所以模型看到的是整个 registry 的 schema 视图。

### 2.3 默认工具集合

`src/openharness/tools/__init__.py::create_default_tool_registry()` 把几类能力拼在一起：

- Bash / File Read / Write / Edit / Notebook
- Glob / Grep / LSP
- Skill / ToolSearch / WebFetch / WebSearch
- Config / Brief / Sleep
- Plan mode / Worktree
- Cron
- Task 管理
- Agent / SendMessage / TeamCreate / TeamDelete
- MCP resources / MCP tools

这说明 Toolkit 在 OpenHarness 中不是“外设”，而是 Harness 的一部分。
 更准确地说：**Harness Toolkit = 工具协议 + 注册表 + schema 暴露 + 权限 / hook 接入点**。

### 2.4 关键设计点

真正重要的不是工具数量，而是这个执行顺序，来自 `src/openharness/engine/query.py::_execute_tool_call()`：

1. PreToolUse hook
2. 找到 tool
3. Pydantic 校验 input
4. 解析 file path / command 供权限器使用
5. PermissionChecker.evaluate()
6. 必要时调用 `permission_prompt`
7. `tool.execute()`
8. PostToolUse hook
9. 返回 ToolResultBlock

所以 Toolkit 不是裸调用，而是 **受控工具总线**。

------

## 3. Context & Memory

这部分是 OpenHarness 很像“真正 Harness”而不是“套壳 agent”的原因。

## 3.1 Prompt 组装不是单一 system prompt

`src/openharness/prompts/context.py::build_runtime_system_prompt()` 会拼接多段上下文：

- base system prompt
- fast mode section
- reasoning settings
- available skills section
- `CLAUDE.md`
- issue / PR comments
- memory section
- relevant memories

也就是说，OpenHarness 的上下文不是一份固定 prompt，而是一套 **动态装配的运行时上下文包**。

### 3.2 Skills 是 prompt 层的一部分

`_build_skills_section()` 会把技能列表写进 system prompt，告诉模型：

- 有哪些 skill
- 当需求匹配 skill 时，应该先调用 `skill` 工具加载详细说明

这让 Skills 不只是目录扫描结果，而是 **模型决策上下文的一部分**。

### 3.3 持久记忆

`src/openharness/memory/memdir.py::load_memory_prompt()` 定义了一个很简单但很实用的模式：

- 项目下有持久 memory 目录
- 入口文件是 `MEMORY.md`
- system prompt 会注入 memory 目录和 MEMORY.md 内容
- 提醒模型把 durable context 存进去

这不是向量数据库那种重系统，而是 Markdown-first 的低复杂度持久记忆。

### 3.4 相关记忆检索

`src/openharness/memory/search.py::find_relevant_memories()` 也很朴素：

- query tokenize
- metadata match 权重 2x
- body match 权重 1x
- 按 score + modified_at 排序

没有上 embedding，但对于 CLI / code agent 场景其实够用了。

### 3.5 上下文压缩

这部分是 OpenHarness 最值得借鉴的。

`src/openharness/services/compact/__init__.py` 里做了两层 compact：

#### 第一层：Microcompact

```
microcompact_messages()
```

它不会总结历史，而是先把旧的、可压缩工具结果内容替换成：

```
[Old tool result content cleared]
```

适用于：

- bash
- read_file
- grep
- glob
- web_search
- web_fetch
- edit_file
- write_file

这非常实用，因为很多 token 实际都浪费在旧工具输出上。

#### 第二层：Full compact

```
compact_conversation()
```

如果 microcompact 之后还超阈值，就调用模型生成结构化 summary，把旧消息替换成：

- 一条 summary message
- 最近若干轮原文保留

#### 第三层：Auto compact

```
auto_compact_if_needed()
```

它被直接嵌入 `run_query()` 的每轮开头。
 这意味着 compact 不是人工操作，而是 Loop 内建机制。

### 3.6 本质

OpenHarness 的 Context & Memory 本质上是四层：

1. prompt assembly
2. persistent markdown memory
3. relevant memory retrieval
4. automatic conversation compaction

------

## 4. Governance

这一层是 OpenHarness 真正的“Harness”边界。

## 4.1 PermissionChecker

`src/openharness/permissions/checker.py::PermissionChecker.evaluate()` 的顺序很清楚：

1. denied tools
2. allowed tools
3. path rules
4. denied command patterns
5. permission mode

支持三种模式：

- `FULL_AUTO`
- `PLAN`
- `DEFAULT`

语义是：

- `FULL_AUTO`：全放行
- `PLAN`：禁止一切 mutating tools
- `DEFAULT`：只读自动放行，写操作要求确认

### 4.2 它为什么有效

因为 OpenHarness 工具层做了统一归一化：

- `_resolve_permission_file_path()`
- `_extract_permission_command()`

也就是说，权限器不是瞎猜，而是能拿到：

- 目标路径
- 目标命令
- 工具名
- 是否只读

这就形成了真正可执行的 policy plane。

## 4.3 HookExecutor

`src/openharness/hooks/executor.py` 是第二层治理。

支持的 hook 类型有：

- command hook
- http hook
- prompt hook
- agent hook

执行方式：

- command hook：跑 shell subprocess
- http hook：POST 到外部服务
- prompt / agent hook：用模型判断某个事件是否允许通过

而且 hook 可以 `block_on_failure`。

### 4.4 生命周期事件

Hook 被接到：

- session start / end
- pre tool use
- post tool use

所以 Governance 在 OpenHarness 里不是一个单点 permission checker，而是：

**Permission rules + approval prompt + lifecycle hooks**

------

## 5. Swarm Coordination

这是 OpenHarness 最“重”的一层，但也是最容易被误抄的地方。

## 5.1 它的真正形态

Swarm 不是“多开几个 agent”。

它由四个东西组成：

1. 子代理启动工具
2. 消息回送通道
3. 后台任务生命周期
4. 协调者 prompt 规范

### 5.2 AgentTool

```
src/openharness/tools/agent_tool.py
```

`agent` 工具接受：

- description
- prompt
- subagent_type
- model
- command
- team
- mode

然后会：

- 如果给了 `subagent_type`，读取 agent definition
- 解析 backend registry
- 优先 `in_process`
- 不行再 subprocess
- 生成 `TeammateSpawnConfig`
- 调 `executor.spawn()`

### 5.3 SendMessageTool

```
src/openharness/tools/send_message_tool.py
```

它做两种路由：

- 普通 background task：写 stdin
- swarm agent：走 mailbox / backend message delivery

这意味着子代理不是“一次性 fire-and-forget”，而是可持续对话的。

### 5.4 BackgroundTaskManager

```
src/openharness/tasks/manager.py
```

这里维护：

- task record
- process handle
- stdout log file
- stop / restart / write_to_task
- agent task / shell task 统一管理

所以 Swarm 并不是纯 prompt 层面的概念，而是和任务系统耦合的。

### 5.5 Coordinator mode

最关键的是 `src/openharness/coordinator/coordinator_mode.py::get_coordinator_system_prompt()`。

这里定义了一个非常明确的 coordinator operating model：

- 协调者负责分解、综合、决策
- worker 负责 research / implementation / verification
- worker 的结果通过 `<task-notification>` XML 作为“用户消息”回送
- 协调者必须自己综合研究结果，不能把“理解工作”继续甩给 worker
- 强调并行
- 强调 continue vs spawn 的选择
- 强调验证必须独立于实现

这个设计是成熟的，因为它把多 agent 的失败模式都写进 prompt 规则了。

### 5.6 In-process backend

`src/openharness/swarm/in_process.py` 很值得学，但不该直接照搬。

它的关键点：

- `contextvars` 做 per-agent context isolation
- mailbox 做 agent 间异步消息
- abort controller 处理 graceful cancel / force cancel
- `start_in_process_teammate()` 在进程内跑 teammate query loop
- backend 维护 active task registry

这套设计适合“一个 Python 进程内跑多个子 agent”。

------

# 二、AegisSec 应该怎么移植

先说结论：

**AegisSec 不应该照搬 OpenHarness 的 CLI-first / 通用代码助手形态，而应该移植它的 Harness 内核。**

你要移植的是：

- 运行时装配思想
- 可控 Agent Loop
- 统一工具协议
- 上下文装配 / compact / memory
- policy + approval + hooks
- in-process swarm / task 协调

你不该移植的是：

- CLI/TUI 体系
- 通用 provider/profile/auth 管理
- tmux / iTerm2 / ohmo 这些终端导向能力
- 以代码仓库编辑为中心的工具分组

------

## 1. AegisSec 当前状态：已经具备哪些 Harness 雏形

从你的仓库来看，当前最接近 Harness 内核的是两处：

### 1.1 `apps/api/app/services/chat_runtime.py`

这里已经实现了一个基础版 Agent Loop：

- 构造 messages
- 给模型传 tools schema
- 解析 OpenAI / Anthropic tool calls
- 调用 `execute_tool`
- 把 tool result 再塞回 messages
- 继续跑下一轮
- 直到得到最终文本
- 还有 `MAX_TOOL_STEPS` 的 budget 控制

这已经是 OpenHarness `run_query()` 的轻量版。

### 1.2 `apps/api/app/api/routes_chat.py`

这里实际上已经做了很多 Harness 外围基础设施：

- generation queue / cancel / worker lifecycle
- transcript segment 持久化
- reasoning trace 持久化
- tool call started / finished / failed 事件
- graph update
- session event broker
- skill autoroute
- capability prompt fragment
- MCP tool inventory
- runtime execute 边界

所以你现在的问题不是“缺 agent loop”，而是：

**这些能力还散在 route + service + compat + runtime 里，没有被抽成一个真正统一的 Harness 层。**

------

## 2. 移植原则：不要重写，先抽象

对 `AegisSec` 最好的做法是：

**把 OpenHarness 的 Harness 内核迁移成一个新的 `app/harness/` 层，然后让现有 `routes_chat.py` 调这个层。**

而不是：

- 重写 chat runtime
- 推翻当前 generation/event/trace 模型
- 把现有 Skills/MCP/Runtime 体系换掉

------

# 三、针对 5 类 Harness 功能的定向迁移方案

------

## 1. Agent Loop：迁移方案

## 1.1 目标

把你现在 `chat_runtime.py` 里分散在 OpenAI / Anthropic runtime 内部的 loop，升级成一个 **独立的 Query Engine**。

### 现在的问题

当前 AegisSec 的 loop 还带有几个限制：

- loop 主要嵌在 provider runtime 里，而不是独立 engine
- tool execution 逻辑在 `routes_chat.py::_build_tool_executor()` 中硬编码
- 没有并发工具执行总线
- 没有 pending continuation 机制
- 没有统一 usage / cost tracker
- compact / governance / hook 还没成为 loop 的第一等成员

### 应该怎么做

建议新建：

```
apps/api/app/harness/
├─ runtime.py
├─ query_engine.py
├─ query_loop.py
├─ messages.py
├─ stream_events.py
└─ usage.py
```

### 推荐职责

#### `runtime.py`

构建 `AegisRuntimeBundle`，类似 OpenHarness 的 `build_runtime()`：

- provider client
- tool registry
- permission checker
- hook executor
- memory service
- compact service
- prompt assembler
- query engine
- graph sink
- transcript sink
- event sink

#### `query_engine.py`

持有：

- messages
- model
- system prompt
- max turns
- total usage
- pending continuation state

#### `query_loop.py`

实现真正的：

```
while not done:
    messages = compact_if_needed(messages)
    assistant_turn = stream_model(messages, tools, system_prompt)
    if no_tool_use:
        return final
    tool_results = execute_tool_calls(...)
    messages.append(tool_results_message)
```

### 必须增加的能力

#### 并发工具执行

OpenHarness 在多工具时会 `asyncio.gather`。
 AegisSec 也应该有这个能力，但要加一个安全分层：

- 只读工具可并发
- 修改 runtime / 写文件 / 写图谱 / 写状态的工具串行
- 高风险 pentest 工具一律串行

所以你不能原样照搬，而是要有：

```
parallelizable = classify_tool_calls(tool_calls)
readonly_results = await gather(...)
mutating_results = await run_serial(...)
```

#### continue_pending

如果一次 generation 在工具结果之后中断，应该允许从 tool_results 继续，而不是强制用户重新提问。

#### usage / cost tracker

对 pentest agent 来说，这很重要，因为多阶段扫描很容易烧 token。

------

## 2. Harness Toolkit：迁移方案

## 2.1 目标

把现在 `routes_chat.py::_build_tool_executor()` 中“if tool_name == ...”的实现，重构成 **统一 ToolRegistry + ToolProtocol**。

### 现在的问题

你现在已经有工具能力，但它们是“路由内部逻辑”，不是 first-class tool object。

当前主要是：

- `execute_kali_command`
- `list_available_skills`
- `execute_skill`
- `read_skill_content`
- MCP tools

这已经足够做第一版 registry。

## 2.2 推荐结构

```
apps/api/app/harness/tools/
├─ base.py
├─ registry.py
├─ kali_command.py
├─ skills.py
├─ mcp.py
├─ graph.py
├─ approvals.py
└─ planning.py
```

### `base.py`

定义：

- `BaseTool`
- `ToolExecutionContext`
- `ToolResult`
- `is_read_only()`
- `risk_level()`
- `capability_tags()`
- `evidence_effects()`

这里我建议你比 OpenHarness 多加三类字段，因为你是 pentest agent：

#### `risk_level`

例如：

- `low`
- `medium`
- `high`
- `destructive`

#### `scope_requirement`

这个工具是否必须命中授权 scope 才可执行。

#### `evidence_effects`

工具结果会不会更新：

- Evidence Graph
- Attack Graph
- Hypothesis state
- Artifact index

### 2.3 为什么 AegisSec 需要比 OpenHarness 更强的工具元数据

因为你不是通用代码 agent，而是安全测试 agent。
 在你的场景里，工具不只是“执行动作”，还带有：

- 风险等级
- 目标资产约束
- 审批要求
- 法律 / 授权边界
- 证据语义

OpenHarness 的 `is_read_only()` 够代码 agent 用，但对 AegisSec 不够。

### 2.4 工具分层建议

#### 第一类：观察类工具

- HTTP fetch
- 目录 / 内容读取
- 指纹 / 轻量扫描
- Skills / MCP inventory

#### 第二类：验证类工具

- 受控 payload 探测
- API replay
- 有超时和频率限制的命令执行

#### 第三类：状态变更类工具

- 写文件
- 更新 graph
- 更新 memory
- task state mutate

#### 第四类：协作类工具

- spawn subagent
- send message
- stop task
- merge evidence

------

## 3. Context & Memory：迁移方案

这是你最应该吸收 OpenHarness 的部分之一。

## 3.1 AegisSec 当前短板

你现在已经有：

- capability prompt fragment
- skill autoroute
- conversation history budget

但你还缺：

- 独立 prompt assembler
- durable memory 层
- context compaction
- graph-aware retrieval

## 3.2 不要照搬 `CLAUDE.md`，要改成 AegisSec 语义

OpenHarness 适合：

- repo context
- CLAUDE.md
- issue / PR comments
- MEMORY.md

AegisSec 应改成：

```
System Prompt
+ Session Objective
+ Authorization & Scope
+ Current Workflow Phase
+ Capability Inventory
+ Skill Context
+ Relevant Memory
+ Evidence Summary
+ Attack Graph Snapshot
+ Pending Hypotheses
+ Recent Transcript
```

### 也就是说：

你的上下文核心不是 repo coding context，而是 **测试目标与证据态**。

## 3.3 推荐模块

```
apps/api/app/harness/prompts/
├─ assembler.py
├─ budget.py
├─ evidence_context.py
├─ memory_context.py
└─ scope_context.py
```

### `assembler.py`

统一拼：

- system prompt
- session prompt
- runtime policy
- active plan summary
- graph summary
- loaded skills
- MCP capabilities
- selected memory snippets
- recent messages

## 3.4 持久记忆怎么做

建议沿用 OpenHarness 的低复杂度思路，但换成 AegisSec 语义：

```
memory/
├─ MEMORY.md
├─ targets/
├─ credentials/
├─ tactics/
├─ findings/
└─ playbooks/
```

### 例如

- `targets/example.com.md`
- `findings/example.com-auth.md`
- `tactics/sso-misconfig.md`
- `playbooks/graphql-introspection.md`

### Memory 检索不应只看用户 query

AegisSec 要检索的键不应该只来自用户自然语言，还要来自：

- 当前 target
- 当前 workflow phase
- graph active nodes
- active hypotheses
- 当前工具结果中的新实体

所以建议 `find_relevant_memories()` 的输入改成：

```
MemoryQuery(
    user_text=...,
    target_scope=...,
    active_phase=...,
    active_hypotheses=[...],
    newly_observed_entities=[...],
)
```

## 3.5 Context compact：强烈建议移植

OpenHarness 的 compact 很适合你，只是 compact 对象要换。

### AegisSec 最该 compact 的内容

不是普通聊天文本，而是：

- 旧 command stdout/stderr
- 重复 HTTP response body
- 老的 tool result transcript
- 已归档的 reasoning trace
- 已经入图的 observation 原文

### 推荐策略

#### 第一层：microcompact

清理旧命令输出正文，只保留：

- command
- exit_code
- key observation
- artifact pointer
- evidence node id

#### 第二层：graph-backed compact

当历史过长时，不是只做文本摘要，而是把旧阶段压成：

- phase summary
- key evidence ids
- key hypothesis transitions
- artifact references

这比 OpenHarness 更适合 pentest 工作流。

------

## 4. Governance：迁移方案

这是 AegisSec 绝对不能少的一层，而且要比 OpenHarness 更强。

## 4.1 你当前已有基础

你现在已经有：

- RuntimePolicy
- 审批机制
- RuntimeOperationError / PolicyViolation
- session-level runtime policy
- route 中的 tool failure / event / trace 处理

但这还不是统一 governance plane。

## 4.2 推荐结构

```
apps/api/app/harness/governance/
├─ checker.py
├─ policy_models.py
├─ approvals.py
├─ hooks.py
└─ decisions.py
```

### `checker.py`

统一替代现在分散的 runtime / route 判断逻辑。

输入至少要有：

- tool_name
- risk_level
- is_read_only
- command
- target
- scope_hit
- workflow_phase
- session policy
- user approval state

输出：

- allow
- deny
- require_approval
- require_scope_confirmation
- require_phase_transition
- reason

## 4.3 AegisSec 必须新增的治理维度

OpenHarness 的 path / command rules 还不够。
 你至少要再加四类：

### ① Scope governance

命令 / HTTP 请求 / 扫描目标是否落在授权范围。

### ② Attack-surface governance

某些动作只允许在某 phase 做。
 例如：

- `Environment Discovery` 可以做轻量枚举
- `Validation` 才允许受控 payload
- `Exploit` 类动作必须更高审批

### ③ Risk-class governance

例如：

- read-only
- intrusive
- exploitative
- destructive
- credential-touching

### ④ Rate / depth governance

防止 agent 在真实目标上无限制扫。

## 4.4 Hook 系统也值得迁移

OpenHarness 的 HookExecutor 思路很适合你，但你要改用途：

### 适合 AegisSec 的 hooks

#### `PreToolUse`

- scope 校验
- payload lint
- 禁止未授权 exploit 类动作
- 敏感命令二次审查

#### `PostToolUse`

- observation extraction
- IoC / secret detection
- evidence node generation
- graph edge inference
- report fragment drafting

#### `PrePhaseTransition`

- 没有足够证据，不允许进入 exploit / synthesis

#### `PostEvidenceIngest`

- 自动唤起 reflector 判断是否 replanning

这会比 OpenHarness 的通用 pre/post-tool hook 更贴合你的场景。

------

## 5. Swarm Coordination：迁移方案

这里要非常克制。

## 5.1 不建议先照搬 OpenHarness 的 team/tmux 体系

你是 Web-first、本地优先、单体优先。
 OpenHarness 的 swarm 有一部分是为 CLI / pane / background task 交互设计的。

对你来说，第一阶段最适合的是：

**进程内 subagent + task record + mailbox + coordinator prompt**
 不要先搞 tmux，不要先搞复杂 backend detection。

## 5.2 AegisSec 最合适的 swarm 形态

你 README 已经定了四个逻辑角色：

- Coordinator
- Planner
- Executor
- Reflector

我建议把 OpenHarness 的 “worker” 思路改造成 AegisSec 的 **专业化 subagent 池**：

### 推荐的第一批 subagent

- `planner_agent`
- `recon_agent`
- `validator_agent`
- `reflector_agent`
- `reporter_agent`

### 第二批再加

- `web_agent`
- `api_agent`
- `ctf_web_agent`
- `artifact_triage_agent`

## 5.3 实现方式

建议目录：

```
apps/api/app/harness/swarm/
├─ registry.py
├─ mailbox.py
├─ in_process_backend.py
├─ task_manager.py
├─ coordinator.py
├─ notifications.py
└─ agent_profiles.py
```

### `in_process_backend.py`

直接借鉴 OpenHarness 的思路：

- `contextvars` 做 agent-local context
- `AbortController`
- mailbox queue
- active task registry

### `task_manager.py`

你现有 generation manager 关注的是“主对话 generation”。
 这里需要一个新的 **subtask manager**，管理：

- subagent id
- role
- parent session id
- current objective
- status
- started_at
- latest summary
- linked evidence ids
- linked graph node ids

### `notifications.py`

OpenHarness 用 XML `<task-notification>`。
 你在 Web/JSON 系统里没必要保持 XML，建议改成结构化对象：

```
{
  "type": "subagent_notification",
  "agent_id": "recon_agent@session_x",
  "status": "completed",
  "summary": "...",
  "result": "...",
  "usage": {...},
  "artifacts": [...],
  "evidence_ids": [...],
  "graph_updates": [...]
}
```

## 5.4 协调者 prompt 要移植，但要改写

OpenHarness 的 `get_coordinator_system_prompt()` 很成熟，但你不能直接拿来。

你要保留的原则：

- 协调者负责综合，不负责把理解工作外包
- research / implementation / verification 可并行
- worker prompt 必须自包含
- continue vs spawn 要有明确规则
- verification 必须独立

你要增加的原则：

- 所有动作必须尊重授权 scope
- 证据必须入图
- exploit 不是默认 phase
- verifier 要验证“漏洞存在性与边界”，不是只看命令成功
- report agent 只消费证据，不直接推断未验证事实

------

# 四、我建议的最终落地架构

最适合你的不是“OpenHarness for pentest”，而是：

**AegisSec Harness = OpenHarness 内核 + AegisSec 的 graph / runtime / policy / pentest workflow 语义**

推荐目录大致这样拆：

```
apps/api/app/
├─ harness/
│  ├─ runtime.py
│  ├─ query_engine.py
│  ├─ query_loop.py
│  ├─ messages.py
│  ├─ stream_events.py
│  ├─ usage.py
│  ├─ tools/
│  ├─ prompts/
│  ├─ memory/
│  ├─ compact/
│  ├─ governance/
│  └─ swarm/
├─ services/
│  ├─ chat_runtime.py          # 降级为 provider adapters
│  ├─ runtime.py               # Kali executor 继续保留
│  ├─ capabilities.py          # 继续保留，作为 registry 数据源之一
│  └─ ...
├─ compat/
│  ├─ skills/
│  └─ mcp/
└─ api/
   └─ routes_chat.py           # 改为调用 harness runtime
```

------

# 五、分阶段移植顺序

## Phase 1：先抽 Harness Runtime，不改业务语义

把这些从 `routes_chat.py` 和 `chat_runtime.py` 抽出来：

- prompt assembly
- tool registry
- tool execution adapter
- query loop
- event sink interface

这一阶段目标：**行为不变，但结构成型**。

## Phase 2：接入 Governance Plane

统一所有工具执行前的：

- scope check
- risk class
- approval
- denied command rules
- post-tool evidence hook

这一阶段目标：**所有工具都经过同一条治理链**。

## Phase 3：接入 Context & Memory

加入：

- prompt assembler
- durable memory
- relevant memory retrieval
- transcript compact
- evidence-aware compact

这一阶段目标：**长会话可持续运行**。

## Phase 4：接入 In-process Swarm

先做：

- planner_agent
- recon_agent
- validator_agent
- reflector_agent

这一阶段目标：**把你 README 里的逻辑角色变成真正的子执行体**。

## Phase 5：Graph-native coordination

让 subagent 的输入输出直接挂接：

- Task Graph
- Evidence Graph
- Causal Graph

这一阶段目标：**agent 行为真正由证据态驱动，而不是只由聊天历史驱动**。

------

# 六、哪些 OpenHarness 功能最值得你直接借鉴

如果只挑最值的 6 个点：

1. `build_runtime()` 这种 **Runtime 装配思路**
2. `run_query()` 这种 **独立的 tool-aware loop**
3. `BaseTool + ToolRegistry + Pydantic schema` 这种 **工具协议**
4. `build_runtime_system_prompt()` 这种 **上下文装配方式**
5. `auto_compact_if_needed()` 这种 **上下文压缩机制**
6. `PermissionChecker + HookExecutor + coordinator prompt` 这种 **治理与协作边界**