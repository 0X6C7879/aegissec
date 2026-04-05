# API 约定

本文档是模块 E 的契约对齐文档，描述当前仓库已经落地并用于前后端联调的接口约定。

## 1. 契约源

- 后端 OpenAPI：`apps/api/app/main.py`
- 导出脚本：`python scripts/export_api_schema.py`
- 前端消费入口：`apps/web/src/lib/api.ts`
- 导出结果：`apps/web/src/generated/api-schema.json`

新增接口或字段时，先更新后端 schema，再重新导出 OpenAPI 文件，最后调整前端类型与调用。

## 2. 响应包裹格式

默认返回使用统一 envelope：

```json
{
  "data": {},
  "meta": {
    "request_id": null,
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 42
    },
    "sort": {
      "by": "updated_at",
      "direction": "desc"
    }
  }
}
```

规则：

- 列表接口在 `meta.pagination` 返回分页信息。
- 支持排序的接口在 `meta.sort` 返回排序字段。
- 204 响应无 body。
- 错误以 FastAPI `detail` 或统一错误对象返回，前端通过 `readErrorMessage()` 解包。

## 3. 当前核心 DTO / 类型映射

`TODO.md` 里早期使用了 `SessionDTO`、`GraphNodeDTO` 等占位命名；当前仓库以如下真实类型为准：

| 领域 | 后端类型 | 前端类型 |
| --- | --- | --- |
| Session 列表/详情 | `SessionRead` / `SessionDetail` | `SessionSummary` / `SessionDetail` |
| Graph | `SessionGraphRead` | `SessionGraph` / `SessionGraphNode` / `SessionGraphEdge` |
| Skills | `SkillRecordRead` | `SkillRecord` |
| MCP | `MCPServerRead` / `MCPToolInvokeResponse` | `MCPServer` / `MCPToolInvokeResponse` |
| Runtime | `RuntimeHealthRead` / `RuntimeStatusRead` | `RuntimeHealth` / `RuntimeStatusResponse` |
| History / Logs | `RunLogRead` | `SessionHistoryEntry` |

## 4. 路由分组

### 4.1 Projects

- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{project_id}`
- `PATCH /api/projects/{project_id}`
- `DELETE /api/projects/{project_id}`
- `POST /api/projects/{project_id}/restore`
- `GET /api/projects/{project_id}/settings`
- `PATCH /api/projects/{project_id}/settings`

### 4.2 Sessions / Chat / Events

- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
- `PATCH /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/pause`
- `POST /api/sessions/{session_id}/resume`
- `POST /api/sessions/{session_id}/cancel`
- `DELETE /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/restore`
- `GET /api/sessions/{session_id}/history`
- `GET /api/sessions/{session_id}/artifacts`
- `POST /api/sessions/{session_id}/chat`
- `WS /api/sessions/{session_id}/events`

### 4.3 Graphs

- `GET /api/sessions/{session_id}/graphs/task`
- `GET /api/sessions/{session_id}/graphs/evidence`
- `GET /api/sessions/{session_id}/graphs/causal`
- `GET /api/sessions/{session_id}/graphs/attack`
- `GET /api/workflows/{run_id}/graphs/task`
- `GET /api/workflows/{run_id}/graphs/evidence`
- `GET /api/workflows/{run_id}/graphs/causal`
- `GET /api/workflows/{run_id}/graphs/attack`

说明：run-scoped workflow graph 路由当前仍作为图谱兼容查询面保留，但不再代表一组公开的 workflow 管理 API。

### 4.4 Runtime

- `GET /api/runtime/health`
- `GET /api/runtime/status`
- `GET /api/runtime/runs`
- `GET /api/runtime/artifacts`
- `POST /api/runtime/start`
- `POST /api/runtime/stop`
- `POST /api/runtime/execute`
- `POST /api/runtime/upload`
- `GET /api/runtime/download`
- `POST /api/runtime/artifacts/cleanup`

### 4.5 Skills

- `GET /api/skills`
- `GET /api/skills/skill-context`
- `GET /api/skills/{skill_id}`
- `GET /api/skills/{skill_id}/content`
- `POST /api/skills/scan`
- `POST /api/skills/rescan`
- `POST /api/skills/refresh`
- `POST /api/skills/{skill_id}/toggle`
- `POST /api/skills/{skill_id}/enable`
- `POST /api/skills/{skill_id}/disable`

Skills 相关返回体在保留历史字段的前提下，已增加一组兼容 Claude Code / OpenCode 技能抽象的扩展字段。前端和 Agent 可按需消费，旧调用方可忽略：

- 通用扩展字段：
  - `source_kind`
  - `loaded_from`
  - `invocable`
  - `conditional`
  - `active`
  - `dynamic`
  - `when_to_use`
  - `allowed_tools`
  - `context`
  - `agent`
  - `effort`
  - `aliases`
  - `paths`
  - `shell_enabled`
  - `prepared_invocation`
  - `resolved_identity`
- Agent 摘要字段额外包含：
  - `user_invocable`
  - `argument_hint`
  - `active_due_to_touched_paths`

约束：

- `execute_skill` 只返回 server-side prepared invocation，真实执行仍必须进入现有 runtime / approval / tool pipeline。
- MCP bridge skill 一律 `invocable=false`、`shell_enabled=false`。
- legacy command 与 bundled 目前是保守桥接语义，不代表已实现独立 slash-command 执行器。

### 4.6 MCP

- `POST /api/mcp/import`
- `POST /api/mcp/register`
- `GET /api/mcp/servers`
- `GET /api/mcp/servers/{server_id}`
- `POST /api/mcp/servers/{server_id}/toggle`
- `POST /api/mcp/servers/{server_id}/enable`
- `POST /api/mcp/servers/{server_id}/disable`
- `POST /api/mcp/servers/{server_id}/refresh`
- `POST /api/mcp/servers/{server_id}/health`
- `POST /api/mcp/servers/{server_id}/tools/{tool_name}/invoke`

## 5. 事件流

Session WebSocket 当前已使用下列事件：

- `session.created`
- `session.updated`
- `message.created`
- `message.updated`
- `workflow.run.started`
- `workflow.stage.changed`
- `task.planned`
- `workflow.task.updated`
- `task.started`
- `task.finished`
- `workflow.approval.required`
- `graph.updated`

## 6. 前后端对齐原则

1. 不再使用 `TODO.md` 早期的 `/prompt`、`/messages`、`/control` 等旧占位命名。
2. 会话输入统一走 `POST /api/sessions/{session_id}/chat`。
3. 日志与历史统一走 `GET /api/sessions/{session_id}/history`。
4. 图谱查询优先走 session 维度接口；run-scoped workflow graph 仅保留兼容查询用途。
5. 前端环境变量仅暴露 `VITE_*` 前缀；服务端配置统一走 `.env` / `.env.local`。
