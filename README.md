# AegisSec Agent

AegisSec Agent 是一个**通用的渗透测试智能体**。它不是单纯的聊天壳，也不是把若干工具拼接起来的自动化脚本，而是一个以**规划、执行、反思、证据沉淀**为核心闭环的 Agent 系统。

项目目标很明确：

- 面向真实渗透测试流程，而不是只做单点扫描
- 以 **Python** 为主，优先采用成熟第三方库，降低手写基础设施成本
- 本地优先、单体优先，保证个人开发者可以持续维护
- 兼容 **OpenCode** 等通用 Agent 生态中的 **Skills / MCP**
- 提供简洁明了的 Web UI，用统一界面完成对话、规划、执行观察、图谱分析与能力管理

AegisSec Agent 设计为一套**可扩展的通用渗透智能体框架**。它既能覆盖常见 Web/SRC/CTF/云与内网评估场景，也能覆盖多阶段、多任务、需要上下文连续性的复杂测试流程。

---

## 1. 核心理念

### 1.1 通用，而不是比赛定制

本项目不是围绕某个单一赛题做硬编码，也不把流程写死为固定闯关逻辑。

它的定位是：

- 以通用渗透测试任务为核心
- 用统一的运行时和工作流覆盖多种测试场景
- 通过任务模板、Skills、MCP、角色策略，适配不同环境

因此它可以覆盖但不限于以下场景：

- Web 应用安全评估
- SRC / 众测常见漏洞发现
- CVE 复现与验证
- 云资产与 API 安全测试
- OA / 多层网络环境的信息收集与任务推进
- 基础内网与主机侧评估
- CTF / 靶场类多步骤推理与验证

### 1.2 Agent first，而不是 tool first

很多“AI 安全工具”的问题是：

- 会调工具，但不会规划
- 会输出命令，但不会整理证据
- 会跑一步，但不会基于结果调整路线
- 会堆功能，但难以解释为什么这么做

AegisSec Agent 的重点不是“能调多少工具”，而是把整个过程做成一个可追踪闭环：

1. 明确目标
2. 制定计划
3. 拆解任务
4. 执行并观察结果
5. 根据证据修正假设
6. 沉淀图谱与报告

### 1.3 本地优先、单体优先

项目第一阶段采用：

- FastAPI 单体后端
- React 单页前端
- SQLite 持久化
- Kali Docker 执行环境

这样做的原因不是“简陋”，而是为了保证：

- 开发速度快
- 调试简单
- 部署成本低
- 结构可控
- 后续再逐步扩展 Redis / PostgreSQL / 多 Worker 时不会推翻整体设计

---

## 2. 设计参考与取舍

本项目在设计上吸收了两个优秀方向：

### 来自 LuaN1aoAgent 的启发

- 用 **Planner / Executor / Reflector** 拆分认知职责
- 用 **任务图 / 因果图** 驱动决策，而不是线性脚本
- 让 Web UI 直接体现任务演化与节点状态
- 强调反思与纠偏，而不是单向执行

参考：
- https://github.com/SanMuzZzZz/LuaN1aoAgent

### 来自 CyberStrikeAI 的启发

- 用清晰的 Web 控制台承载配置、对话、工具、知识、Agent 管理
- 通过 **Role / Skills / Tools / MCP** 做能力组合
- 尽量用配置化方式管理工具和角色，而不是写死在代码里
- 把“可管理性”做成产品能力，而不仅是工程内部细节

参考：
- https://github.com/Ed1s0nZ/CyberStrikeAI

### 本项目的取舍

AegisSec Agent 不会直接照搬这两个项目，而是做如下取舍：

- 保留 **角色分工 + 任务图 + 证据图 + Web 控制台**
- 保留 **Skills / MCP / 配置化扩展**
- 去掉过重的平台化设计
- 保持 UI 简洁，不堆砌大量低频页面
- 先做“通用可用”，再做“丰富全面”

---

## 3. 功能概览

### 3.1 核心闭环

- 对话驱动任务创建
- 自动生成计划与任务树
- 将任务表示为 DAG
- 在 Kali Docker 内执行受控操作
- 记录日志、产出物、观察结果
- 将证据沉淀为任务图 / 证据图 / 因果图
- 支持人工审批与中途干预
- 最终输出可复盘的执行结果与报告

### 3.2 能力层

- Runtime：Kali Docker 执行器
- Skills：兼容通用 Agent Skill 目录，做识别与调用辅助
- MCP：兼容本地 / 远程 MCP server，做发现、启停、能力展示与调用
- LLM：统一模型抽象层
- Graph：任务图、证据图、因果图
- Memory：会话上下文、运行日志、节点历史

### 3.3 Web UI

- Workspace 主工作台
- Skills 管理页
- MCP 管理页
- Projects / History / Settings
- 右侧图谱与日志洞察面板
- 审批卡与控制动作

---

## 4. 系统架构

```text
┌──────────────────────────────────────────────────────┐
│                    Web UI (React)                   │
│  Workspace / Skills / MCP / History / Settings     │
└───────────────────────┬──────────────────────────────┘
                        │ HTTP / WS
┌───────────────────────▼──────────────────────────────┐
│                FastAPI Application                   │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ API Layer                                      │  │
│  │ sessions / tasks / graphs / skills / mcp       │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ Agent Core                                     │  │
│  │ Coordinator / Planner / Executor / Reflector  │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ Orchestration Layer                            │  │
│  │ workflow / approvals / policies / retries      │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ Compatibility Layer                            │  │
│  │ skill discovery / mcp registry / adapters      │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │ Persistence                                    │  │
│  │ SQLite + SQLAlchemy + artifact storage         │  │
│  └────────────────────────────────────────────────┘  │
└──────────────┬───────────────────────┬───────────────┘
               │                       │
               │                       │
      ┌────────▼────────┐     ┌────────▼─────────┐
      │ Kali Runtime    │     │ External Systems │
      │ Docker Executor │     │ LLM / MCP / FS   │
      └─────────────────┘     └──────────────────┘
```

---

## 5. Agent 设计

### 5.1 角色划分

#### Coordinator
负责整个会话的状态推进。

职责：
- 接收用户目标
- 选择 workflow 模板
- 协调 Planner / Executor / Reflector
- 维护会话状态机
- 决定何时进入审批
- 汇总结果给前端

#### Planner
负责规划与拆解。

职责：
- 生成阶段计划
- 将阶段计划转换为任务树和 DAG
- 标记依赖关系
- 识别可并行节点
- 在执行中根据新证据调整图结构

#### Executor
负责受控执行。

职责：
- 调用 Runtime / Skill / MCP 能力
- 执行单步动作
- 采集 stdout / stderr / artifact
- 记录结构化结果
- 把发现同步给图谱层

#### Reflector
负责总结与纠偏。

职责：
- 判断结果是否支持当前假设
- 识别失败类型
- 给出下一步修正建议
- 决定是继续、回退还是终止

### 5.2 为什么不用更多 Agent

第一阶段不做大量微服务化 Agent。

原因：
- 个人项目需要低复杂度
- 多进程 / 多队列 / 多上下文同步的维护成本高
- 先把核心闭环做通比扩充角色更重要

因此当前方案是：

- **逻辑多角色**
- **进程内实现**
- **统一编排**

后续如有必要，再把部分能力演进成独立 subagent。

---

## 6. 通用工作流

### 6.1 工作流目标

让 Agent 在不同场景下遵循相同的抽象过程：

1. 目标理解
2. 计划制定
3. 环境识别
4. 信息收集
5. 假设生成
6. 低风险验证
7. 证据更新
8. 路径修正
9. 结果汇总

### 6.2 标准工作流阶段

#### Phase 0 - Session Bootstrap
- 创建会话
- 绑定项目配置
- 选择运行时与策略
- 加载 Skills / MCP 能力快照

#### Phase 1 - Goal Parsing
- 解析用户目标
- 识别场景类型
- 提炼范围、约束、优先级
- 输出规范化任务说明

#### Phase 2 - Planning
- 生成阶段计划
- 构建 DAG
- 标记关键节点与审批点
- 生成第一轮执行候选

#### Phase 3 - Environment Discovery
- 探测目标环境类型
- 收集基础上下文
- 识别资产、服务、端口、入口、配置线索
- 把结果写入 Evidence Graph

#### Phase 4 - Hypothesis Formation
- 根据环境与线索形成假设
- 建立“观察 → 假设 → 待验证项”的关系
- 给每个假设分配优先级和证据等级

#### Phase 5 - Validation
- 对高优先级假设做受控验证
- 每一步都保留输入、输出、超时、退出码、工件
- 若触发审批规则，则暂停等待确认

#### Phase 6 - Reflection & Replanning
- Reflector 判断是否形成有效进展
- 若失败则归因：工具失败 / 环境阻断 / 路径错误 / 证据不足
- Planner 基于失败原因修正任务图

#### Phase 7 - Synthesis
- 整理会话摘要
- 更新任务图与证据图最终状态
- 导出结果、结论、时间线与工件索引

---

## 7. UI 设计

### 7.1 设计原则

UI 必须做到：

- 简洁
- 重点明确
- 高信息密度但不混乱
- 不做花哨动画
- 对任务推进和图谱理解有帮助

### 7.2 页面结构

#### Workspace
主页面，占使用频率最高。

三栏布局：

- 左栏：Sessions / Task Tree / Agents
- 中栏：对话流 / 输入区 / 审批卡 / 摘要卡
- 右栏：Task Graph / Evidence Graph / Logs / Node Detail

#### Skills
只做：
- 扫描
- 识别
- 展示
- 启用 / 禁用
- 兼容性诊断

#### MCP
只做：
- 导入
- 启停
- 能力发现
- 工具 / 资源 / prompts 展示
- 健康检查

#### Projects / History / Settings
做轻量管理，不做复杂后台。

### 7.3 UI 不做什么

- 不做大而全的数据总览仪表盘
- 不做低频但复杂的 SaaS 管理模块
- 不把每项能力拆成单独重页面
- 不把用户视线从 Workspace 主工作台上频繁打断

---

## 8. 技术选型

### 后端
- Python 3.11+
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- Uvicorn
- httpx
- structlog
- networkx

### 前端
- React
- TypeScript
- Vite
- Tailwind CSS
- shadcn/ui
- React Flow 或 Cytoscape.js
- TanStack Query
- Zustand

### 执行环境
- Docker SDK for Python
- Kali Linux container

### 兼容层
- YAML / Markdown 解析
- MCP client（stdio / http）
- 本地配置扫描器

---

## 9. 快速开始

```bash
git clone <your-repo-url>
cd aegissec
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
make dev
```

启动后：

- API: http://localhost:8000
- Web: http://localhost:3000
- Docs: http://localhost:8000/docs

---

## 10. 项目目录

```text
aegissec/
├─ apps/
│  ├─ api/                 # FastAPI app
│  └─ web/                 # React app
├─ aegis/
│  ├─ agent/               # Coordinator / Planner / Executor / Reflector
│  ├─ workflow/            # Workflow template and state machine
│  ├─ runtime/             # Kali Docker executor
│  ├─ skills/              # Skill discovery and compatibility layer
│  ├─ mcp/                 # MCP registry and client adapters
│  ├─ graph/               # task graph / evidence graph / causal graph
│  ├─ persistence/         # models, repositories, migrations
│  ├─ services/            # orchestration service, session service
│  ├─ schemas/             # pydantic models
│  └─ utils/
├─ configs/
├─ data/
├─ artifacts/
├─ tests/
├─ docker/
├─ scripts/
├─ docs/
└─ README.md
```

---

## 11. 路线图

### v0.2
- Workspace 主界面
- 会话创建与任务树
- Kali Runtime
- Task Graph
- 基础日志流
- Skills / MCP 扫描与展示

### v0.3
- Evidence Graph
- 审批机制
- Reflector 纠偏
- 报告导出
- 项目空间与历史回放

### v0.4
- 角色模板
- Workflow 模板中心
- 更丰富的工件管理
- PostgreSQL / Redis 可选支持

---

## 12. 免责声明

本项目仅用于**授权的安全测试、研究和教学用途**。

使用者必须保证：

- 已获得明确授权
- 遵守当地法律法规
- 在隔离环境中测试高风险能力
- 对自身使用行为承担全部责任

作者与贡献者不对任何未授权使用及其后果负责。
