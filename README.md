# AegisSec

AegisSec 是一个本地优先的通用渗透测试智能体工作台，围绕 **会话编排、受控执行、图谱沉淀、Skills / MCP 兼容** 四条主线构建。当前项目已经形成可本地运行的单体 MVP：后端使用 FastAPI + SQLModel，前端使用 React + Vite，执行面使用 Kali Docker Runtime，并提供会话、图谱、Runtime、Skills、MCP、Settings 等完整工作台页面。

---

## 核心定位

AegisSec 不是单纯的聊天壳，也不是把若干安全工具拼接起来的脚本集合。

它当前的定位是：

- 用 **Session** 组织一次完整的安全评估过程
- 用 **Generation Queue + Events** 驱动消息生成、排队、暂停、恢复与注入
- 用 **Task / Evidence / Causal / Attack Graph** 追踪过程与结果
- 用 **Kali Runtime** 承载受控命令执行与工件落盘
- 用 **Skills / MCP Compatibility Layer** 管理外部能力与上下文拼装
- 用统一的 **Web Workbench** 完成会话、图谱、运行时与能力管理

---

## 当前已经实现的功能

### 1. 会话与项目管理

- Project 的创建、查询、更新、删除、恢复
- Project 级默认设置读取与更新
- Session 的创建、查询、更新、删除、恢复
- Session 与 Project 关联
- Session 状态流转：运行、暂停、恢复、取消
- Session 会话详情、对话、队列、回放、历史记录、工件列表

### 2. 对话与生成队列

- 基于 Session 的聊天接口
- 活跃生成与排队生成的分离读取
- 活跃生成取消
- 会话级取消，连带清理活跃与排队生成
- 对暂停中的生成做 continuation resolve
- 对运行中的生成注入上下文
- 消息编辑、再生成、分叉、回滚
- WebSocket 事件流推送

### 3. 图谱能力

- Session 维度图谱读取：
  - Task Graph
  - Evidence Graph
  - Causal Graph
  - Attack Graph
- Workflow Run 维度图谱读取：
  - Task Graph
  - Evidence Graph
  - Causal Graph
  - Attack Graph
- 前端提供攻击图为主的可视化工作区，并与会话流联动

### 4. Runtime 执行面

- Runtime 状态查询
- Runtime 健康检查
- Runtime 启动 / 停止
- Runtime 命令执行
- Runtime 执行记录查询
- Runtime 工件查询
- Runtime 工件上传 / 下载
- Runtime 工件清理与执行记录清空
- Session 运行时策略解析与校验

### 5. Skills 兼容层

- Skills 列表读取
- Skill 单项详情读取
- Skill 内容读取
- Skill 启用 / 禁用
- Skill 扫描 / 重扫 / 刷新
- Skill Context 构建
- Skill 编排预览（orchestration plan preview）
- Web 端技能搜索、筛选、详情抽屉与上下文状态展示

### 6. MCP 管理层

- 从配置导入 MCP 服务器
- 手动注册 stdio / http MCP server
- MCP 服务器列表 / 详情读取
- MCP 服务器启用 / 禁用
- MCP 服务器能力刷新
- MCP 服务器健康检查
- MCP 服务器删除
- MCP Tool 直接调用
- Web 端同时提供服务器视角和能力平铺视角

### 7. Settings 与本地模型配置

- 读取用户级模型 API 设置
- 更新用户级模型 API 设置
- 支持 OpenAI 风格配置
- 支持 Anthropic 风格配置
- 响应中不回显密钥明文，仅暴露是否已配置

### 8. Web Workbench

当前前端路由已经落下：

- `/sessions`
- `/sessions/:sessionId/chat`
- `/sessions/:sessionId/graph`
- `/skills`
- `/skills/:skillId`
- `/mcp`
- `/mcp/:serverId`
- `/runtime`
- `/settings`

当前主界面由统一的 `WorkbenchShell` 承载，包含侧边导航、抽屉折叠、移动端菜单与工作区主体。

---

## 系统架构

```text
AegisSec
├── apps/web                  # React 工作台
│   ├── Sessions / Graph
│   ├── Skills
│   ├── MCP
│   ├── Runtime
│   └── Settings
│
├── apps/api                  # FastAPI 单体后端
│   ├── Projects / Sessions
│   ├── Chat / Queue / Replay / Events
│   ├── Runtime / Artifacts
│   ├── Skills / MCP
│   ├── Graphs
│   └── Settings / Auth / Health
│
├── apps/api/data             # SQLite / Runtime Workspace / 本地数据
├── docker/kali               # Kali Runtime 镜像
├── config                    # Workflow / 示例配置
├── scripts                   # dev / check / schema / seed
└── docker-compose.yml        # API + Web + Kali Runtime 联调入口
```

### 后端主入口

`apps/api/app/main.py` 当前已经挂载以下路由：

- health
- auth
- projects
- sessions
- chat
- runtime
- graphs
- workflow graphs
- settings
- skills
- mcp

### 前端主入口

`apps/web/src/App.tsx` 当前已经接入以下工作台组件：

- Session Workspace
- Graph Workbench
- Skills Workbench
- MCP Workbench
- Runtime Workspace
- Settings Workbench
- Workbench Shell

---

## 技术栈

### 后端

- Python 3.12+
- FastAPI
- SQLModel
- Alembic
- Docker SDK for Python
- httpx
- mcp[cli]
- PyYAML
- LangGraph
- Uvicorn

### 前端

- React 19
- TypeScript
- Vite
- React Router
- TanStack React Query
- Zustand
- React Flow
- dagre
- react-markdown
- Vitest
- ESLint
- Prettier

### 运行与存储

- SQLite
- Kali Linux Docker Container
- 本地 `.env` / `.env.local`

---

## 快速开始

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd aegissec
```

### 2. Ubuntu 24 一键搭建并启动

前置条件：请使用**具备 sudo 权限的普通用户**执行；脚本会拒绝 root 直接运行。

```bash
bash scripts/bootstrap_ubuntu.sh all
```

这个入口面向全新 Ubuntu 24 主机，会按顺序完成：

- 安装系统依赖与开发基础工具
- 安装 uv、Node.js、Corepack/pnpm、Docker
- 自动从 `.env.example` 生成 `.env`（如果尚不存在）
- 同步 API / Web 依赖并构建 Kali Runtime 镜像
- 执行 `python3 scripts/check.py` 的完整校验链路
- 后台启动 API 和 Web 开发服务

> `all` 首次执行可能耗时较长，因为它会跑完整项目校验与评估脚本，而不只是最小化 smoke test。

默认开发访问地址（服务监听默认绑定到 `0.0.0.0`）：

- API: `127.0.0.1:8000`
- Web: `127.0.0.1:5173`

常用生命周期命令：

```bash
bash scripts/bootstrap_ubuntu.sh status
bash scripts/bootstrap_ubuntu.sh stop
```

- 运行日志位于 `.aegissec/logs/`
- 如果这是第一次安装 Docker，当前 shell 可能还未拿到最新的 `docker` 组权限；重新登录，或者执行一次 `newgrp docker` 后再直接使用无 `sudo` 的 `docker`
- `docker` 组通常等价于对 Docker daemon 的高权限访问；只应在你信任的本机开发环境中这样配置

### 3. 前台开发模式

```bash
python scripts/dev.py
```

该脚本适合已经完成环境准备后的前台联调，会：

- 同步 API 依赖
- 安装 Web 依赖
- 启动 FastAPI 开发服务
- 启动 Vite 开发服务

如果你只想单独启动某一侧，也可以使用仓库中的单侧启动脚本。

### 4. 单独执行全量检查

```bash
python scripts/check.py
```

当前检查链路包括：

- `scripts/sync_requirements.py`
- API 依赖同步
- Ruff
- Black
- MyPy
- Pytest
- `ci/lint_skills.py --strict`
- `ci/reduce_skill.py`
- `ci/eval_routing.py`
- `ci/eval_task.py`
- `ci/report_metrics.py --strict-thresholds --write-registry --last-verified-model gpt-5.4`
- OpenAPI 导出
- 前端安装
- ESLint
- Prettier 检查
- Vitest
- TypeScript 构建
- Vite build

### 5. 写入演示数据

```bash
python scripts/seed_demo.py
```

该脚本会创建：

- `Demo Workspace` 项目
- `Authorized Assessment Demo` 会话

并向该会话写入一条初始聊天消息，方便直接验证 Sessions / Runtime / Skills / MCP 页面联调。

### 6. Docker Compose 联调

```bash
docker compose up --build
```

Compose 当前会启动：

- `api`
- `web`
- `kali-runtime`

---

## 环境变量

当前 `.env.example` 中已经提供的主要变量包括：

```env
AEGISSEC_APP_NAME=aegissec
AEGISSEC_API_HOST=0.0.0.0
AEGISSEC_API_PORT=8000
AEGISSEC_WEB_HOST=0.0.0.0
AEGISSEC_WEB_PORT=5173
AEGISSEC_FRONTEND_ORIGIN=http://127.0.0.1:5173
AEGISSEC_API_AUTH_MODE=disabled
AEGISSEC_API_AUTH_TOKEN=
AEGISSEC_QUEUE_BACKEND=in_process
AEGISSEC_REDIS_URL=
AEGISSEC_KALI_IMAGE=aegissec-kali:latest
AEGISSEC_RUNTIME_CONTAINER_NAME=aegissec-kali-runtime
AEGISSEC_RUNTIME_WORKSPACE_CONTAINER_PATH=/workspace
AEGISSEC_RUNTIME_DEFAULT_TIMEOUT_SECONDS=300
AEGISSEC_RUNTIME_RECENT_RUNS_LIMIT=10
AEGISSEC_RUNTIME_RECENT_ARTIFACTS_LIMIT=20
AEGISSEC_MCP_IMPORT_PATHS=[]
LLM_API_KEY=
LLM_API_BASE_URL=
LLM_DEFAULT_MODEL=
VITE_API_BASE_URL=http://127.0.0.1:8000
```

说明：

- 默认鉴权模式为 `disabled`
- 默认队列后端为 `in_process`
- 默认 Runtime 镜像为 `aegissec-kali:latest`
- 默认数据库为 `apps/api/data/aegissec.db`

---

## API 概览

### Health / Auth

- `GET /api/health`
- `GET /api/auth/status`

### Projects

- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{project_id}`
- `PATCH /api/projects/{project_id}`
- `DELETE /api/projects/{project_id}`
- `POST /api/projects/{project_id}/restore`
- `GET /api/projects/{project_id}/settings`
- `PATCH /api/projects/{project_id}/settings`

### Sessions

- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
- `PATCH /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/pause`
- `POST /api/sessions/{session_id}/resume`
- `POST /api/sessions/{session_id}/cancel`
- `DELETE /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/restore`
- `GET /api/sessions/{session_id}/conversation`
- `GET /api/sessions/{session_id}/queue`
- `GET /api/sessions/{session_id}/replay`
- `GET /api/sessions/{session_id}/history`
- `GET /api/sessions/{session_id}/artifacts`
- `POST /api/sessions/{session_id}/continuations/{continuation_token}/resolve`
- `POST /api/sessions/{session_id}/generations/active/inject`
- `WS  /api/sessions/{session_id}/events`

### Runtime

- `GET /api/runtime/status`
- `GET /api/runtime/health`
- `GET /api/runtime/runs`
- `GET /api/runtime/artifacts`
- `POST /api/runtime/start`
- `POST /api/runtime/stop`
- `POST /api/runtime/execute`
- `GET /api/runtime/profiles`
- `POST /api/runtime/upload`
- `GET /api/runtime/download`
- `POST /api/runtime/artifacts/cleanup`
- `POST /api/runtime/runs/clear`

### Graphs

- `GET /api/sessions/{session_id}/graphs/task`
- `GET /api/sessions/{session_id}/graphs/evidence`
- `GET /api/sessions/{session_id}/graphs/causal`
- `GET /api/sessions/{session_id}/graphs/attack`
- `GET /api/workflows/{run_id}/graphs/task`
- `GET /api/workflows/{run_id}/graphs/evidence`
- `GET /api/workflows/{run_id}/graphs/causal`
- `GET /api/workflows/{run_id}/graphs/attack`

### Skills

- `GET /api/skills`
- `GET /api/skills/skill-context`
- `POST /api/skills/orchestration-plan`
- `GET /api/skills/{skill_id}`
- `GET /api/skills/{skill_id}/content`
- `POST /api/skills/scan`
- `POST /api/skills/rescan`
- `POST /api/skills/refresh`
- `POST /api/skills/{skill_id}/toggle`
- `POST /api/skills/{skill_id}/enable`
- `POST /api/skills/{skill_id}/disable`

### MCP

- `POST /api/mcp/import`
- `POST /api/mcp/register`
- `GET /api/mcp/servers`
- `GET /api/mcp/servers/{server_id}`
- `DELETE /api/mcp/servers/{server_id}`
- `POST /api/mcp/servers/{server_id}/toggle`
- `POST /api/mcp/servers/{server_id}/enable`
- `POST /api/mcp/servers/{server_id}/disable`
- `POST /api/mcp/servers/{server_id}/refresh`
- `POST /api/mcp/servers/{server_id}/health`
- `POST /api/mcp/servers/{server_id}/tools/{tool_name}/invoke`

### Settings

- `GET /api/settings/model-api`
- `PUT /api/settings/model-api`

---

## 前端工作区说明

### Sessions Workspace

当前主工作区已经支持：

- Session 列表与切换
- 新建、重命名、归档、恢复会话
- 对话消息流展示
- 运行中的 generation 队列与中断
- 活跃生成上下文注入
- Attack Graph 与会话流并排展示
- 节点级编辑、再生成、分叉、回滚
- Slash Catalog 注入与 UI 导航动作

### Skills Workbench

- Skill 搜索
- Skill 列表
- Skill 详情弹层
- 启用 / 禁用
- 参数 Schema 查看
- Skill Context 查看
- SKILL.md 原文查看

### MCP Workbench

- 服务器总览
- 工具 / 资源 / Prompts 平铺视图
- 手动注册 stdio / http 服务器
- 服务器详情
- 健康检查
- 刷新能力
- 启停与删除
- 直接调用 MCP Tool

### Runtime Workspace

- Runtime 启停
- 健康状态查看
- 直接提交命令
- 查看 stdout / stderr / exit code
- 最近执行记录清理

### Settings Workbench

- 本地模型 API 配置
- OpenAI / Anthropic 参数持久化
- 密钥是否已配置状态回显

---

## 项目目录

```text
aegissec/
├── .github/workflows/
├── apps/
│   ├── api/
│   │   ├── alembic/
│   │   ├── app/
│   │   │   ├── api/
│   │   │   ├── compat/
│   │   │   ├── core/
│   │   │   ├── db/
│   │   │   ├── graphs/
│   │   │   ├── harness/
│   │   │   ├── services/
│   │   │   └── workflows/
│   │   ├── data/
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── web/
│       ├── src/
│       ├── package.json
│       └── pnpm-lock.yaml
├── config/
│   ├── examples/
│   └── workflows/
├── docker/
│   └── kali/
├── docs/
├── scripts/
├── skills/
├── .env.example
├── docker-compose.yml
├── TODO.md
└── README.md
```

---

## 当前阶段判断

按当前源码状态，AegisSec 已经不是只有文档的方案仓库，而是一个能本地跑通主链路的单体工作台项目。当前最清晰的主链路是：

**Project / Session → Chat / Queue / Events → Runtime → Graphs → Skills / MCP → Settings / Replay / History**

还没有必要在这个阶段把 README 写成“大而全平台”的终局文档；更适合的是把它描述为：

- 一个可本地运行的 MVP
- 一个以 Session 为中心的安全智能体工作台
- 一个已经具备 Runtime、Graph、Skills、MCP 和 Workbench 主闭环的项目

---

## 免责声明

本项目仅用于 **授权的安全测试、研究和教学用途**。

你必须确保：

- 已获得明确授权
- 遵守适用法律法规
- 在隔离环境中使用高风险执行能力
- 对自己的使用行为承担全部责任

作者与贡献者不对任何未授权使用及其后果负责。
