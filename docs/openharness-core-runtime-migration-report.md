# OpenHarness core runtime architecture subset for AegisSec

## Scope and source-of-truth

- **Authoritative upstream repo inspected:** `HKUDS/OpenHarness`
- **Authoritative docs used:** upstream `README.md` plus Python source under `src/openharness/`
- **Important naming trap:** `https://www.open-harness.dev/` is a **different** TypeScript project (`MaxGfeller/open-harness`), not `HKUDS/OpenHarness`. It is useful only as a same-name ecosystem reference, **not** as architecture evidence for this migration.

## Executive conclusion

For AegisSec, the reusable OpenHarness kernel is **not** the CLI/TUI app. The reusable part is the chain:

1. **streaming message/tool loop**
2. **tool contract + registry**
3. **conversation compaction**
4. **permission + hook governance around tool execution**
5. **background task + in-process teammate coordination**

The clearest current composition point is:

- `src/openharness/ui/runtime.py` → `build_runtime_bundle(...)`

That file is useful as a **wiring reference only**. AegisSec should recreate the composition in FastAPI services/websocket session runtime, **not** port `ui/runtime.py` wholesale.

---

## 1. Runtime spine that matters

### 1.1 Message and event contracts

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/engine/messages.py` → `ConversationMessage`, `TextBlock`, `ToolUseBlock`, `ToolResultBlock` | Canonical in-memory conversation structure for user turns, assistant text, tool requests, and tool results | Reuse the **shape**, not necessarily the exact classes. This is the right model for AegisSec session state and websocket event replay |
| `src/openharness/engine/stream_events.py` → `AssistantTextDelta`, `AssistantTurnComplete`, `ToolExecutionStarted`, `ToolExecutionCompleted`, `ErrorEvent`, `StatusEvent` | Normalized runtime event bus between engine and UI | Good fit for AegisSec websocket streaming and audit timeline |
| `src/openharness/api/client.py` → `ApiMessageRequest`, `ApiTextDeltaEvent`, `ApiMessageCompleteEvent`, `ApiRetryEvent`, `SupportsStreamingMessages` | LLM-provider boundary the engine depends on | Keep the **protocol boundary** (`SupportsStreamingMessages`) so AegisSec can swap Anthropic/OpenAI/custom backends without changing the loop |

### 1.2 Query engine / tool loop

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/engine/query.py` → `QueryContext` | Aggregates runtime dependencies: API client, tool registry, permission checker, cwd, model, prompts, hooks, metadata | This should become an AegisSec `SessionRuntimeContext` or equivalent DI bundle |
| `src/openharness/engine/query.py` → `run_query(...)` | Core loop: stream assistant output → inspect tool calls → permission check → execute tool(s) → append tool results → continue until no tool use | **Primary kernel concept to migrate**. This is the backbone AegisSec wants |
| `src/openharness/engine/query.py` → `_execute_tool_call(...)` | Centralizes validation, permission enforcement, hook execution, tool execution, and tool-result normalization | Strong candidate for an AegisSec `ToolExecutionService.execute_model_tool_call(...)` |
| `src/openharness/engine/query.py` → `_resolve_permission_file_path(...)`, `_extract_permission_command(...)` | Extracts normalized path/command data before permission evaluation | Useful for AegisSec policy enforcement on filesystem/process/network tools |
| `src/openharness/engine/query_engine.py` → `QueryEngine` | Session-scoped wrapper holding message history, usage, `submit_message(...)`, and `continue_pending(...)` | Good fit for AegisSec per-session runtime object stored behind session services |

### 1.3 Evidence from tests

- `tests/test_engine/test_query_engine.py`
  - verifies plain replies
  - verifies tool execution loop
  - verifies retry/status propagation
  - verifies pre-tool hook blocking

This is the best upstream test anchor for the loop behavior.

### Migration note

Port the **loop semantics**, not the product shell:

- keep streaming deltas
- keep structured tool events
- keep append-tool-results-and-continue behavior
- move persistence, approvals, evidence capture, and UI streaming into FastAPI/WebSocket services

---

## 2. Tool registry subset that matters

### 2.1 Core tool abstraction

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/tools/base.py` → `BaseTool` | Standard async tool contract with `execute(...)`, `input_model`, `is_read_only(...)`, `to_api_schema()` | This abstraction is directly relevant. AegisSec already has capability/tool layers; this is a clean reference shape |
| `src/openharness/tools/base.py` → `ToolExecutionContext` | Shared runtime context for tools (`cwd`, metadata) | Map to AegisSec runtime execution context carrying session, workspace, approvals, evidence handles |
| `src/openharness/tools/base.py` → `ToolResult` | Normalized tool return with `output`, `is_error`, `metadata` | Good minimal tool result contract |
| `src/openharness/tools/base.py` → `ToolRegistry` | Name → tool implementation registry plus API schema export | This is the registry concept worth carrying over |

### 2.2 Composition example only

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/tools/__init__.py` → `create_default_tool_registry(...)` | Registers the full built-in OpenHarness tool set and MCP adapters | **Do not port wholesale.** Use this only as an example of how to assemble a registry from multiple capability providers |

### 2.3 Swarm/task tools that matter as API shape

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/tools/task_create_tool.py` → `TaskCreateTool` | Model-facing tool to start background shell/agent tasks | Relevant if AegisSec exposes background execution to the model |
| `src/openharness/tools/agent_tool.py` → `AgentTool` | Model-facing spawn interface for teammate/subagent execution | Good reference for subagent spawning tool shape |
| `src/openharness/tools/send_message_tool.py` → `SendMessageTool` | Sends follow-up work/messages to running agent tasks or swarm agents | Useful if AegisSec keeps long-lived background workers |

### Recommendation

Port only the **registry/contract pattern**. Re-register AegisSec-native tools instead of importing OpenHarness file/bash/web tooling mechanically.

---

## 3. Context compaction subset that matters

### 3.1 Exact upstream implementation

Primary module:

- `src/openharness/services/compact/__init__.py`

Important symbols:

- `COMPACTABLE_TOOLS`
- `estimate_message_tokens(...)`
- `microcompact_messages(...)`
- `get_compact_prompt(...)`
- `format_compact_summary(...)`
- `build_compact_summary_message(...)`
- `AutoCompactState`
- `compact_conversation(...)`
- `auto_compact_if_needed(...)`

Integration point:

- `src/openharness/engine/query.py` lines around `run_query(...)` start-of-turn auto-compact call

### 3.2 What is actually worth migrating

The useful architecture is the **two-stage compaction strategy**:

1. **cheap microcompact**: clear bulky old tool-result bodies first
2. **full summary compact**: if still over threshold, ask the LLM to summarize older history and replace it with a synthetic summary turn

### 3.3 AegisSec mapping

This maps well to AegisSec because your sessions can easily accumulate:

- long shell output
- scan output
- large artifact summaries
- repeated planner/executor/reflector cycles

Recommended adaptation:

- keep the **microcompact** idea almost directly
- adapt the **full summary** step to produce both:
  - an injected short runtime summary for the next LLM call
  - a durable session/evidence artifact in DB/storage
- make compaction aware of AegisSec-specific bulky blocks (runtime command output, graph diff summaries, artifact previews)

### 3.4 Evidence from tests

- `tests/test_services/test_compact.py`

Note: this test file currently covers mainly legacy summary helpers, so the source code is more authoritative than the tests for auto-compact behavior.

---

## 4. Governance subset that matters

### 4.1 Permission model

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/config/settings.py` → `PathRuleConfig`, `PermissionSettings` | Typed permission config: mode, allowed/denied tools, path rules, denied commands | Good base schema for AegisSec policy config |
| `src/openharness/permissions/checker.py` → `PermissionDecision` | Result object: allow / require confirmation / reason | Reusable concept for approval UX/API |
| `src/openharness/permissions/checker.py` → `PermissionChecker.evaluate(...)` | Central decision point using tool name, read-only flag, path, command | Worth porting conceptually almost 1:1 |

Modes are backed by:

- `src/openharness/permissions/modes.py`

### 4.2 Hook model

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/hooks/events.py` → `HookEvent` | Lifecycle hooks: `session_start`, `session_end`, `pre_tool_use`, `post_tool_use` | Good minimal event surface |
| `src/openharness/hooks/loader.py` → `HookRegistry`, `load_hook_registry(...)` | Collects hooks from settings/plugins | Good if AegisSec wants pluggable policies or automations |
| `src/openharness/hooks/executor.py` → `HookExecutionContext`, `HookExecutor` | Executes command, HTTP, prompt-like, and agent-like hooks | Good policy/automation reference, but execution backends should be AegisSec-controlled |

### 4.3 AegisSec mapping

This is directly relevant to your backend goals:

- **PermissionChecker** → approval and policy gate before runtime/tool execution
- **HookExecutor** → optional policy automations, notifications, pre/post validators
- `run_query(...)/_execute_tool_call(...)` already shows the correct enforcement order:
  1. pre-tool hook
  2. tool lookup + input validation
  3. permission evaluation
  4. optional confirmation
  5. execute tool
  6. post-tool hook

### 4.4 Evidence from tests

- `tests/test_permissions/test_checker.py`
- `tests/test_hooks/test_executor.py`
- `tests/test_engine/test_query_engine.py` also verifies pre-tool hook blocking in the live loop

### 4.5 Port caution

`HookExecutor` supports shell/HTTP/prompt hooks. AegisSec should preserve the **hook contract**, but execute hooks under your existing security boundaries and approval model rather than trusting the upstream defaults.

---

## 5. Swarm coordination subset that matters

## 5.1 Background task substrate

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/tasks/types.py` → `TaskRecord`, `TaskType`, `TaskStatus` | Runtime task state model | Good template for AegisSec background worker/job records |
| `src/openharness/tasks/manager.py` → `BackgroundTaskManager` | Starts shell/agent subprocess tasks, persists output, supports stop/write/read/update | Relevant conceptually, but AegisSec may want DB-backed jobs instead of singleton in-memory manager |
| `src/openharness/tasks/manager.py` → `get_task_manager()` | Process singleton | **Do not port as-is** into FastAPI; use dependency-injected service or app-scoped manager |

## 5.2 Teammate/subagent data model

Relevant symbols in:

- `src/openharness/swarm/types.py`

Keep these concepts:

- `TeammateSpawnConfig`
- `SpawnResult`
- `TeammateMessage`

These are useful because they define the spawn/message contract independently of the terminal UI.

## 5.3 In-process teammate execution

Primary module:

- `src/openharness/swarm/in_process.py`

Key symbols:

- `TeammateAbortController`
- `TeammateContext`
- `get_teammate_context()` / `set_teammate_context()`
- `start_in_process_teammate(...)`
- `_run_query_loop(...)`
- `InProcessBackend`

Why it matters:

- this is the cleanest upstream example of **subagent execution without tmux/TUI coupling**
- it reuses the same query loop in a worker context
- it isolates worker context with `contextvars`
- it supports graceful cancel vs force cancel

For AegisSec, this is the best upstream reference for **backend-native subagents**.

## 5.4 Mailbox coordination

Primary module:

- `src/openharness/swarm/mailbox.py`

Key symbols:

- `MailboxMessage`
- `TeammateMailbox`
- `create_user_message(...)`
- `create_shutdown_request(...)`
- `create_idle_notification(...)`
- permission request/response message factories later in the file

Why it matters:

- it gives a simple leader/worker message protocol
- it decouples execution from direct UI presence
- it is good inspiration for AegisSec worker signaling, even if you reimplement it with DB rows / Redis / websocket notifications instead of filesystem inboxes

## 5.5 Optional subprocess worker pattern

Module:

- `src/openharness/swarm/subprocess_backend.py`

This is relevant only if AegisSec wants worker isolation via subprocesses. The core idea is fine; the exact stdin/stdout subprocess management is likely less attractive than app-scoped async jobs or explicit worker services inside your FastAPI design.

## 5.6 Evidence from tests

- `tests/test_swarm/test_in_process.py`
- `tests/test_swarm/test_mailbox.py`
- `tests/test_swarm/test_registry.py`
- `tests/test_swarm/test_team_lifecycle.py`
- `tests/test_tasks/test_manager.py`

---

## 6. Supporting context assembly that is relevant (secondary)

These are not the harness loop itself, but they matter if AegisSec wants OpenHarness-like project/skill context injection.

| Upstream file / symbol | What it does | AegisSec mapping |
|---|---|---|
| `src/openharness/prompts/context.py` → `build_runtime_system_prompt(...)` | Assembles base prompt + reasoning settings + skills + CLAUDE.md + memory + issue/PR context | Useful reference for composing AegisSec session context from Skills/MCP/project memory |
| `src/openharness/prompts/claudemd.py` → `discover_claude_md_files(...)`, `load_claude_md_prompt(...)` | Finds and injects project instruction files upward from cwd | Relevant because AegisSec already values project-local instructions and skills |
| `src/openharness/skills/loader.py` / `skills/registry.py` | Loads markdown skills and stores them by name | Relevant if you want skill inventory surfaced into the harness |
| `src/openharness/memory/manager.py` | File-based project memory helpers | Conceptually useful, but the exact file-based memory implementation is weaker than AegisSec’s existing graph/evidence direction |

---

## 7. Modules that should **not** be ported into AegisSec

These are either UI shell, terminal ergonomics, provider-profile/product surfaces, or terminal-specific swarm features.

### 7.1 CLI/TUI shell: do not port

- `src/openharness/cli.py`
- `src/openharness/commands/registry.py`
- `src/openharness/ui/*`
- `frontend/terminal/*`
- `src/openharness/keybindings/*`
- `src/openharness/vim/*`
- `src/openharness/themes/*`
- `src/openharness/output_styles/*`
- `src/openharness/voice/*`

Reason:

- these are presentation/product shell layers for terminal UX
- AegisSec needs FastAPI + web workbench behavior, not Typer/Textual/Ink-style interaction patterns

### 7.2 Provider-profile management and subscription bridges: do not port

Do **not** port these product-specific flows into AegisSec:

- `src/openharness/config/settings.py` sections around:
  - `ProviderProfile`
  - `default_provider_profiles()`
  - `resolve_profile()`
  - flat/legacy provider sync helpers
- `src/openharness/auth/*`
- `src/openharness/api/openai_client.py`
- `src/openharness/api/codex_client.py`
- `src/openharness/api/copilot_client.py`

Reason:

- AegisSec needs a backend model-provider abstraction, but **not** OpenHarness’s CLI-oriented profile/workflow/subscription setup surface
- keep only the abstract `SupportsStreamingMessages` idea and your own provider integration layer

### 7.3 Personal agent / channel product surfaces: do not port

- `ohmo/*`
- `src/openharness/channels/*`
- `src/openharness/bridge/*`

Reason:

- these are separate product surfaces, not harness-kernel concerns

### 7.4 Terminal/pane swarm features: do not port

- `src/openharness/swarm/registry.py` pane/backend detection logic for tmux/iTerm2
- `src/openharness/swarm/worktree.py`
- `src/openharness/swarm/types.py` **pane-oriented** protocols and methods:
  - `PaneBackend`
  - `CreatePaneResult`
  - pane create/show/hide/title/border APIs
- `src/openharness/swarm/team_lifecycle.py` fields and persistence centered on pane-backed teams (for example `tmux_pane_id`)

Reason:

- AegisSec explicitly should **not** inherit tmux/iTerm2/worktree-oriented swarm UX
- if you keep swarm, keep **worker contracts and coordination**, not terminal pane management

### 7.5 Coordinator command shell: port only the concept, not the prompt product

Use cautiously, do not port verbatim:

- `src/openharness/coordinator/coordinator_mode.py`

Keep:

- the idea of coordinator-only tools
- worker notification envelopes
- separation between leader and workers

Do not keep as-is:

- the long coordinator prompt text
- the XML envelope formatting as the product contract unless it fits your API
- the in-memory `TeamRegistry` / `TeamCreateTool` / `TeamDeleteTool` as-is

---

## 8. Best migration subset for AegisSec (recommended cut)

If the goal is “OpenHarness core harness ability inside AegisSec FastAPI backend”, the highest-value subset is:

### Port/adapt first

1. `src/openharness/engine/messages.py`
2. `src/openharness/engine/stream_events.py`
3. `src/openharness/api/client.py` **only the request/event protocol boundary**
4. `src/openharness/tools/base.py`
5. `src/openharness/engine/query.py`
6. `src/openharness/engine/query_engine.py`
7. `src/openharness/services/compact/__init__.py`
8. `src/openharness/permissions/checker.py`
9. `src/openharness/hooks/events.py`
10. `src/openharness/hooks/loader.py`
11. `src/openharness/hooks/executor.py`
12. `src/openharness/tasks/types.py`
13. `src/openharness/tasks/manager.py` **concept only; rework singleton/persistence model**
14. `src/openharness/swarm/types.py` **only teammate/task messaging structures**
15. `src/openharness/swarm/in_process.py`
16. `src/openharness/swarm/mailbox.py` **concept only; likely replace filesystem mailbox with DB/queue**

### Use as reference only

1. `src/openharness/tools/__init__.py`
2. `src/openharness/ui/runtime.py`
3. `src/openharness/prompts/context.py`
4. `src/openharness/prompts/claudemd.py`
5. `src/openharness/skills/loader.py`
6. `src/openharness/skills/registry.py`

### Explicitly exclude

1. `src/openharness/cli.py`
2. `src/openharness/commands/registry.py`
3. `src/openharness/ui/*`
4. `frontend/terminal/*`
5. `src/openharness/keybindings/*`
6. `src/openharness/vim/*`
7. `src/openharness/voice/*`
8. provider-profile/auth/subscription setup flows
9. tmux/iTerm2/pane/worktree swarm machinery
10. `ohmo/*` and channels

---

## 9. Final mapping to AegisSec architecture

### Best conceptual translation

- **OpenHarness `QueryEngine`** → AegisSec `Session Runtime`
- **OpenHarness `ToolRegistry` + `BaseTool`** → AegisSec `Capability Registry / Tool Facade`
- **OpenHarness `PermissionChecker` + `HookExecutor`** → AegisSec `Approval + Policy Layer`
- **OpenHarness `services.compact`** → AegisSec `Context Compression Service`
- **OpenHarness `BackgroundTaskManager` + `InProcessBackend`** → AegisSec `Subagent / Background Job Orchestrator`
- **OpenHarness `TeammateMailbox`** → AegisSec `Worker Message Bus` (prefer DB/queue/WebSocket events over filesystem inboxes)

### What not to translate directly

- terminal commands → API endpoints / websocket actions
- confirmation dialogs → approval records and frontend approval cards
- process singleton state → app-scoped services + persistence
- filesystem mailbox/team files → DB-backed coordination artifacts
- provider-profile CLI workflows → backend provider config service

This is the cleanest way to migrate the **harness kernel** without turning AegisSec into a terminal clone.
