# AegisSec Agent TODO.md

> 目标：将当前版本拆分为 **5 个可并行开发模块**，适合 3~5 人协作推进。
>
> 原则：
> - 每个模块边界清晰，尽量减少相互阻塞
> - 先做主链路可跑通，再补增强功能
> - 所有模块围绕同一个核心目标：**做一个通用的渗透测试智能体**
> - UI 保持简洁明了，以 Workspace 主工作台为中心

---

## 0. 协作方式与分工建议

### 推荐拆分

- **模块 A：平台后端与数据层**
- **模块 B：Agent 编排与图谱引擎**
- **模块 C：Runtime / Skills / MCP 兼容层**
- **模块 D：Web UI / 前端工作台**
- **模块 E：测试、集成、文档与发布**

### 协作规则

- 所有模块必须先对齐接口，再并行开发
- 严禁前后端互相等待“口头约定”
- 所有事件、状态、API、数据模型必须文档化
- 每个模块至少提供：
  - 输入输出定义
  - 最小可运行 demo
  - 自测清单

---

## 1. 统一里程碑

### M1：骨架跑通
目标：单体系统可启动，前后端能通信，能创建 Session。

### M2：主链路跑通
目标：从输入目标 → 生成计划 → 执行受控步骤 → 写入日志 → 前端展示。

### M3：能力接入完成
目标：Skills 扫描、MCP 管理、Kali Runtime 可用。

### M4：图谱与报告可用
目标：任务图 / 证据图 / 因果图可视化，支持结果导出。

### M5：可演示版本
目标：整体可稳定演示，文档完整，具备最小发布条件。

---

# 模块 A：平台后端与数据层

> 负责人方向：FastAPI / SQLAlchemy / SQLite / 基础 API / WebSocket

## A.1 P0

- [x] 初始化后端工程目录
  - [x] `backend/app/api`
  - [x] `backend/app/core`
  - [x] `backend/app/models`
  - [x] `backend/app/schemas`
  - [x] `backend/app/services`
  - [x] `backend/app/db`
- [x] 建立 `pyproject.toml`
- [x] 接入 `FastAPI + Uvicorn + Pydantic v2 + SQLAlchemy 2`
- [x] 建立配置系统
  - [x] `settings.py`
  - [x] `.env.example`
- [x] 建立数据库初始化脚本
- [x] 建立 Alembic 迁移
- [x] 设计并实现基础模型
  - [x] `Project`
  - [x] `Session`
  - [x] `TaskNode`
  - [x] `GraphNode`
  - [x] `GraphEdge`
  - [x] `RunLog`
  - [x] `Artifact`
  - [x] `SkillRecord`
  - [x] `MCPServerRecord`
- [x] 实现基础 API
  - [x] `POST /api/projects`
  - [x] `GET /api/projects`
  - [x] `POST /api/sessions`
  - [x] `GET /api/sessions`
  - [x] `GET /api/sessions/{id}`
  - [x] `POST /api/sessions/{id}/pause`
  - [x] `POST /api/sessions/{id}/resume`
  - [x] `POST /api/sessions/{id}/cancel`
- [x] 建立 WebSocket / SSE 事件推送骨架
- [x] 建立统一响应结构
- [x] 建立统一错误处理与日志中间件

## A.2 P1 应做

- [x] Artifact 文件落盘与索引
- [x] Session 历史查询与过滤
- [x] 分页、排序、模糊搜索
- [x] 简单 Token 鉴权或本地登录模式
- [x] 系统健康检查接口
  - [x] `/api/health`
  - [x] `/api/runtime/health`
- [x] 前后端共享 schema 生成策略

## A.3 P2

- [x] SQLite → PostgreSQL 适配预留
- [x] Redis 队列预留接口
- [x] OpenAPI 文档优化
- [x] 项目级配置与用户级配置分离

## A.4 对外接口约定

### 提供给模块 B
- Session CRUD
- TaskNode 存取
- GraphNode / GraphEdge 持久化
- RunLog 写入接口

### 提供给模块 D
- Projects / Sessions / History API
- WebSocket 订阅
- Session 状态流

### 交付物
- [x] 后端可启动
- [x] 数据库可迁移
- [x] Swagger 可访问
- [x] 基础 CRUD 跑通

---

# 模块 B：Agent 编排与图谱引擎

> 负责人方向：Coordinator / Planner / Executor / Reflector / Workflow / Graph

## B.1 P0 必做

- [x] 定义 Agent Core 目录
  - [x] `agent/coordinator.py`
  - [x] `agent/planner.py`
  - [x] `agent/executor.py`
  - [x] `agent/reflector.py`
  - [x] `agent/workflow.py`
  - [x] `agent/graph_manager.py`
- [x] 定义通用 workflow 模板
  - [x] `recon`
  - [x] `analysis`
  - [x] `validation`
  - [x] `reporting`
- [x] 设计任务节点状态机
  - [x] `pending`
  - [x] `queued`
  - [x] `running`
  - [x] `waiting_approval`
  - [x] `success`
  - [x] `failed`
  - [x] `skipped`
- [x] 实现 Coordinator 主循环
  - [x] 创建执行上下文
  - [x] 加载 workflow
  - [x] 驱动 Planner → Executor → Reflector
  - [x] 更新节点状态
- [x] 实现 Planner 最小版本
  - [x] 根据用户 goal 生成阶段计划
  - [x] 生成任务树
  - [x] 生成 DAG 依赖关系
- [x] 实现 GraphManager 最小版本
  - [x] 任务图生成
  - [x] 证据图节点创建
  - [x] 因果边创建
- [x] 实现 Reflector 最小版本
  - [x] 根据执行结果给出 success / failed / retry 建议
  - [x] 更新节点状态与结论摘要
- [x] 定义统一事件模型
  - [x] `SessionCreated`
  - [x] `TaskPlanned`
  - [x] `TaskStarted`
  - [x] `TaskFinished`
  - [x] `ApprovalRequired`
  - [x] `GraphUpdated`

## B.2 P1

- [x] 支持可并行节点挑选
- [x] 支持计划动态重写
- [x] 支持失败重试策略
- [x] 支持节点级摘要压缩
- [x] 支持证据置信度字段
- [x] 支持图谱快照导出
- [x] 支持 Replay 数据生成

## B.3 P2

- [x] 多 workflow 模板切换
- [x] 子 Agent 专项角色提示词
- [x] 节点优先级重排
- [x] 长会话上下文裁剪策略

## B.4 对外接口约定

### 依赖模块 A
- Session / TaskNode / Graph 持久化接口
- 事件推送接口

### 依赖模块 C
- Runtime 执行接口
- Skill 调用接口
- MCP 调用接口

### 提供给模块 D
- 当前阶段
- 当前活跃 Agent
- 任务树数据
- 任务图 / 证据图 / 因果图 JSON
- 审批请求结构

### 交付物
- [x] 能输入 goal 生成 plan
- [x] 能把 plan 转为任务树和 DAG
- [x] 能驱动最小主循环
- [x] 能输出可供前端渲染的 graph JSON

---

# 模块 C：Runtime / Skills / MCP 兼容层

> 负责人方向：Docker SDK / 执行适配器 / Skills 扫描 / MCP Registry / 调用适配

## C.1 P0

- [x] 建立 Runtime 执行层
  - [x] `app/services/runtime.py`
  - [x] `app/db/models.py`
  - [x] `app/api/routes_runtime.py`
- [x] 使用 Docker SDK for Python 接入 Kali 容器
- [x] 实现最小执行接口
  - [x] 创建容器
  - [x] 执行命令
  - [x] 获取 stdout / stderr
  - [x] 获取退出码
  - [x] 设置 timeout
- [x] 定义 RuntimePolicy
  - [x] 是否允许联网
  - [x] 是否允许写文件
  - [x] 最大执行时长
  - [x] 最大命令长度
- [x] 建立 Skills 兼容层
  - [x] 仅扫描项目根目录 `/skills`
  - [x] 解析 `SKILL.md`
  - [x] 建立 SkillRecord
- [x] 建立 MCP 兼容层
  - [x] 支持手动注册 server
  - [x] 导入 `.mcp.json`
- [x] 实现 MCP Server 能力发现
  - [x] tools
  - [x] resources
  - [x] prompts
- [x] 实现 MCP 启停控制
- [x] 提供统一 Capability 接口给模块 B

## C.2 P1

- [x] Runtime artifact 回收
- [x] 容器文件上传下载
- [x] Skill 参数 schema 提取
- [x] Skill 注入上下文接口
- [x] MCP tool 参数表单 schema 输出
- [x] MCP 连接健康检查
- [x] 调用日志持久化

## C.3 P2

- [x] 支持多个 Runtime profile
- [x] 支持 Streamable HTTP MCP
- [x] 支持本地缓存与能力快照
- [x] 支持导入更多通用 Agent 配置格式

## C.4 对外接口约定

### 提供给模块 B
- `run_command(session_id, task_id, command, policy)`
- `list_skills()`
- `get_skill(skill_id)`
- `list_mcp_servers()`
- `call_mcp_tool(server_id, tool_name, args)`

### 提供给模块 D
- Skills 列表
- Skill 详情
- MCP Servers 列表
- MCP 能力详情
- Runtime 健康状态

### 交付物
- [x] Kali 容器可被后端控制
- [x] Skills 可扫描展示
- [x] MCP Servers 可导入与开关
- [x] 至少 1 个 MCP tool 可跑通

---

# 模块 D：Web UI / 前端工作台

> 负责人方向：React / TypeScript / Tailwind / shadcn/ui / 图谱渲染

## D.1 P0

- [x] 初始化前端工程
  - [x] React + TypeScript + Vite
  - [x] Tailwind CSS
  - [x] shadcn/ui
  - [x] Zustand
  - [x] TanStack Query
- [x] 建立全局布局
  - [x] Sidebar
  - [x] TopBar
  - [x] Main Workspace
- [x] 完成 Workspace 页面
  - [x] 左栏 Session 列表
  - [x] 左栏 Task Tree
  - [x] 中栏 Chat Timeline
  - [x] 中栏 Input Composer
  - [x] 右栏 Tabs（Task Graph / Evidence / Logs）
- [x] 接入 Projects 页面最小版本
- [x] 接入 Skills 页面最小版本
- [x] 接入 MCP 页面最小版本
- [x] 接入 History 页面最小版本
- [x] 接入 Settings 页面最小版本
- [x] 接入 WebSocket / SSE 实时刷新
- [x] 接入状态色与统一 Design Tokens

## D.2 P1

- [x] 审批卡 Approval Card
- [x] 节点详情 Drawer
- [x] Runtime Console 组件
- [x] Graph Canvas 组件封装
- [x] Skills 搜索 / 过滤 / 兼容状态展示
- [x] MCP Server 启停控制
- [x] 会话回放基础页面
- [x] 空状态页与引导页

## D.3 P2

- [x] 深浅主题切换
- [x] 图谱筛选器
- [x] 节点时间轴视图
- [x] 导出视图
- [x] 响应式移动端适配

## D.4 页面要求

### Workspace 必须做到
- [x] 一眼看到当前 session 状态
- [x] 一眼看到当前 phase
- [x] 一眼看到当前任务树
- [x] 一眼看到右侧图谱/日志
- [x] 输入区支持插入 Skill / MCP Tool

### UI 风格要求
- [x] 简洁
- [x] 深色优先
- [x] 控制台式
- [x] 低噪音
- [x] 高信息密度但不杂乱

## D.5 对外依赖

### 依赖模块 A
- Projects / Sessions / History API
- WebSocket 事件流

### 依赖模块 B
- 任务树 JSON
- Graph JSON
- 审批请求结构

### 依赖模块 C
- Skills / MCP / Runtime 状态数据

### 交付物
- [x] 前端可独立启动
- [x] Workspace 主链路可演示
- [x] Skills / MCP 页面可浏览
- [x] 图谱和日志能实时更新

---

# 模块 E：测试、集成、文档与发布

> 负责人方向：联调、测试、CI、演示脚本、README、开发文档

## E.1 P0

- [x] 建立 monorepo 根目录规范
  - [x] `apps/api/`
  - [x] `apps/web/`
  - [x] `docs/`
  - [x] `scripts/`
- [x] 统一 `.editorconfig`
- [x] 统一 `ruff / black / isort / mypy` 规则
- [x] 建立前端 `eslint / prettier` 规则
- [x] 编写开发环境启动脚本
  - [x] `scripts/dev_backend.sh`
  - [x] `scripts/dev_frontend.sh`
  - [x] `scripts/dev_all.sh`
  - [x] `scripts/dev.py`
- [x] 编写 Docker 开发说明
- [x] 编写联调清单
- [x] 编写 README
- [x] 编写开发文档
- [x] 编写 API 约定文档

## E.2 P1

- [x] 后端单元测试
- [x] 前端组件测试
- [x] API 集成测试
- [x] Runtime 冒烟测试
- [x] Skills 扫描测试
- [x] MCP 导入测试
- [x] 演示数据种子
- [x] Demo 视频脚本

## E.3 P2

- [x] GitHub Actions CI
- [x] 自动化构建检查
- [x] 发布说明模板
- [x] Docker Compose 一键启动
- [x] 示例配置包

## E.4 交付物
- [x] 项目能一键启动开发环境
- [x] 基础测试可执行
- [x] 文档自洽
- [x] 能给新协作者 30 分钟内跑起来

---

# 2. 横向接口对齐清单（必须最先完成）

## 2.1 数据结构先行

以下结构已按当前仓库真实契约完成对齐：

- [x] `SessionRead / SessionDetail / SessionSummary`
- [x] `WorkflowTaskNode`
- [x] `SessionGraphNode`
- [x] `SessionGraphEdge`
- [x] `workflow approval payload`
- [x] `RunLogRead / SessionHistoryEntry`
- [x] `SkillRecordRead / SkillRecord`
- [x] `MCPServerRead / MCPServer`
- [x] `MCP capability / tool invoke DTO`
- [x] `RuntimeHealthRead / RuntimeHealth`

## 2.2 事件流先行

- [x] `session.created`
- [x] `session.updated`
- [x] `task.planned`
- [x] `workflow.task.updated`
- [x] `task.finished`
- [x] `graph.updated`
- [x] `workflow.approval.required`
- [x] `message.created`
- [x] `workflow.run.started / workflow.stage.changed`
- [x] `tool.call.started / tool.call.finished / tool.call.failed`

## 2.3 页面 API 先行

### Workspace
- [x] `GET /api/sessions/{id}`
- [x] `GET /api/sessions/{id}/history`
- [x] `GET /api/sessions/{id}/graphs/task`
- [x] `GET /api/sessions/{id}/graphs/evidence`
- [x] `GET /api/sessions/{id}/artifacts`
- [x] `POST /api/sessions/{id}/chat`

### Skills
- [x] `GET /api/skills`
- [x] `POST /api/skills/scan`
- [x] `GET /api/skills/{id}`
- [x] `POST /api/skills/{id}/enable`
- [x] `POST /api/skills/{id}/disable`

### MCP
- [x] `GET /api/mcp/servers`
- [x] `POST /api/mcp/import`
- [x] `POST /api/mcp/servers/{id}/enable`
- [x] `POST /api/mcp/servers/{id}/disable`
- [x] `POST /api/mcp/servers/{id}/refresh`
- [x] `GET /api/mcp/servers/{id}`

---

# 3. 推荐开发顺序

## 第 1 周：先把骨架立起来

- [x] A：后端工程、数据库、基础 Session API
- [x] D：前端工程、基础布局、Workspace 静态页
- [x] E：脚手架、规范、启动脚本
- [x] B：workflow 数据结构与状态机初稿
- [x] C：Runtime / Skills / MCP 目录和接口定义

## 第 2 周：打通主链路

- [x] B：Planner / Coordinator 最小闭环
- [x] C：Kali Runtime 最小执行能力
- [x] A：RunLog / TaskNode / Graph 接口
- [x] D：Workspace 接入真实数据
- [x] E：联调与 demo 用例

## 第 3 周：接入兼容层

- [x] C：Skills 扫描
- [x] C：MCP 导入与启停
- [x] D：Skills 页面 / MCP 页面
- [x] B：Reflector / 图谱更新
- [x] A：更多查询接口

## 第 4 周：补齐可演示能力

- [x] D：审批卡 / 节点详情 / Runtime Console
- [x] B：证据图 / 因果图
- [x] E：测试、README、开发文档、演示脚本
- [x] 全体：Bugfix 与体验优化

---

# 4. 最小验收标准（Definition of Done）

## 系统级 DoD
- [x] 项目可在本地启动
- [x] 前后端可联通
- [x] 能创建项目与 session
- [x] 能输入目标生成 plan
- [x] 能执行至少一条受控 runtime 命令
- [x] 能展示任务树
- [x] 能展示任务图
- [x] 能展示日志
- [x] 能扫描 Skills
- [x] 能导入并展示 MCP Servers
- [x] README 可指导新开发者启动项目

## 模块级 DoD

### A 完成标准
- [x] 所有核心表可迁移
- [x] 基础 API 可用
- [x] WebSocket / SSE 至少一种可用

### B 完成标准
- [x] goal → plan → task graph 主链路完成
- [x] 节点状态变化可持久化

### C 完成标准
- [x] Kali 执行器可控
- [x] Skills / MCP 列表可读

### D 完成标准
- [x] Workspace 可演示完整主路径
- [x] Skills / MCP 页面可操作

### E 完成标准
- [x] 文档齐全
- [x] 测试最小闭环可执行
- [x] Demo 可复现

---

# 5. 风险与阻塞点

- LLM 输出不稳定导致 Planner 结构不一致
- Runtime 与宿主机环境差异导致执行不稳定
- Skills 格式兼容性不一致
- 不同 MCP server 的 transport 差异
- 图谱结构定义反复变化导致前后端返工
- 前端等后端、后端等前端造成空转

## 对策
- [x] 先固定 DTO 和事件流
- [x] Planner 第一版使用受限结构化输出
- [x] Runtime 先只支持最小执行集
- [x] MCP 先只保障导入、展示、启停、基础调用
- [x] Graph 先统一 JSON schema，再做渲染
