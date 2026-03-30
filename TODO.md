# aegissec 开发 TODO

> 依据 `docs/03_开发计划文档.md`、`docs/02_功能实现文档.md`、`docs/01_需求文档_PRD.md`、`docs/00_个人开源版架构设计.md` 整理。

## 使用说明

- 完成某个子任务后，将对应的 `- [ ]` 改为 `- [x]`
- 某阶段全部子任务完成后，再勾选该阶段标题
- 开发优先级遵循 P0 主闭环：`会话创建 -> Skill/MCP 识别 -> Workflow 运行 -> Kali 执行 -> 图谱更新 -> 报告导出`

## 全局约束

- 一切开发任务都应优先服务四类核心场景：SRC 自动化众测与主流漏洞发现、典型 CVE / 云安全 / AI 基础设施漏洞实测、多层网络与 OA 环境下的多步攻击路径规划与权限维持分析、基础域渗透模拟
- 保持单体优先、本地优先、SQLite、WebSocket、Docker/Kali 的 V1 架构，不提前平台化
- 不引入微服务、Redis 队列、图数据库、多租户、Skill/MCP Marketplace
- V1 Skill 兼容目标以识别、解析、展示为主，不承诺全语义兼容
- V1 MCP 兼容目标以导入、启停、发现、最小可调用为主，高级 OAuth/SSE 能力延后
- 优先完成 P0 主链路，再推进 P1/P2 增强项

## 阶段化开发计划

- [x] 阶段 1：工程脚手架（Week 1 / Epic A）
  - [x] 初始化 monorepo 目录：`apps/api`、`apps/web`、`docker/kali`、`config/`
  - [x] 配置后端基础栈：FastAPI、Pydantic v2、SQLModel、pytest、ruff、black、mypy、uv
  - [x] 配置前端基础栈：React、TypeScript、Vite、pnpm、eslint、tsconfig
  - [x] 补齐基础工程文件：`.env.example`、README 初稿、开发启动脚本
  - [x] 搭建 `docker/kali` 基础镜像目录与构建入口
  - [x] 确保 `uvicorn` 可启动 API 服务
  - [x] 确保前端 dev server 可启动
  - [x] 验证本地一键启动可行，基础静态检查可运行

- [x] 阶段 2：Session + Chat（Week 2 / Epic B）
  - [x] 建立 `Session`、`Message` 数据模型与数据表
  - [x] 实现 Session 列表、创建、读取等 API
  - [x] 实现 Chat API 入口与消息持久化
  - [x] 接入 WebSocket 事件推送
  - [x] 完成会话列表页
  - [x] 完成聊天页与基础交互
  - [x] 验证刷新后会话仍可恢复
  - [x] 验证历史消息可正确展示

- [x] 阶段 3：Kali Runtime（Week 3 / Epic C）
  - [x] 集成 Docker SDK for Python
  - [x] 编写 Kali 镜像构建脚本
  - [x] 实现容器生命周期管理
  - [x] 实现命令执行 API
  - [x] 捕获并存储 `stdout`、`stderr`、退出码
  - [x] 实现制品路径登记与 Artifact 记录
  - [x] 完成 Runtime 状态页
  - [x] 验证容器可复用、任务可超时中断、执行结果可在 UI 查看
  - [x] 预装 `kali-linux-default` 工具集，避免运行期工具缺失
  - [x] 执行命令统一切换到 `/bin/zsh` 环境

- [x] 阶段 4：Skill 兼容层（Week 4 / Epic D）
  - [x] 实现 Skill 路径扫描器
  - [x] 实现 `SKILL.md` 解析器
  - [x] 建立 `SkillRecord` 存储模型
  - [x] 实现 `GET /api/skills`
  - [x] 实现 `POST /api/skills/rescan`
  - [x] Skill 扫描固定为项目根目录 `skills/`，不再读取用户级兼容目录
  - [x] 完成 Skills 列表页
  - [x] 完成 Skill 详情抽屉或详情面板
  - [x] 验证非法 Skill 可显示错误信息，兼容层仅承诺识别/解析/展示

- [x] 阶段 5：MCP 兼容层（Week 5 / Epic E）
  - [x] 实现 `.mcp.json` 导入器
  - [x] 实现 `~/.claude.json` 导入器
  - [x] 实现 `opencode.json` 导入器
  - [x] 建立统一的 `MCPServer`、`MCPCapability` 数据模型
  - [x] 集成官方 `mcp` Python SDK
  - [x] 实现 server registry / client manager / capability discovery
  - [x] 实现 MCP Server 启停开关
  - [x] 完成 MCP 管理页
  - [x] 在 UI 展示 tools / resources / prompts
  - [x] 验证至少 1 个 stdio Server 与 1 个远程 HTTP Server 可工作

- [x] 阶段 6：Workflow + Graph（Week 6 / Epic F）
  - [x] 搭建 LangGraph 基础骨架
  - [x] 实现阶段状态机
  - [x] 建立 `WorkflowRun`、`TaskNode`、图节点边等持久化结构
  - [x] 实现任务图构建器
  - [x] 实现因果图构建器
  - [x] 打通 Workflow 事件推送
  - [x] 完成图谱页面
  - [x] 验证从任务创建到报告前一阶段可推进
  - [x] 验证图谱会随状态自动更新

- [ ] 阶段 7：Report + UI 打磨（Week 7 / Epic G）

  - [x] 接入 Pretext 优化 Skills 卡片文本排版
  - [x] 优化工作台布局：固定侧栏、强化折叠按钮标识、统一左右面板对齐
  - [x] 重构为统一对话工作台，合并 Sessions / Runtime 入口
  - [x] 在对话页展示模型推理轨迹与工具调用状态
  - [x] 实现模型自动调用工具，无需人工输入命令
  - [x] 优化关键 UI 交互流程
  - [x] 固定对话发送框到底部，仅消息区滚动
  - [x] 压缩推理与工具抽屉体积，贴近参考对话样式
  - [x] 修复 shell 工具抽屉展示与展开交互
  - [x] 修复消息流内 shell 抽屉在浏览器缩放下被压缩裁切
  - [x] 修复大屏下 shell 抽屉被底部输入框遮挡，并支持命令输出自动换行
  - [x] 统一 shell 抽屉与模型输出框体量，并保持默认折叠
  - [x] 补齐工作台侧边功能导航与最近对话快捷操作
  - [ ] 补齐错误页与空状态页


- [ ] 阶段 8：测试与发布准备（Week 8 / Epic H）

  - [ ] 完成 README、Quickstart、架构说明补充
  - [ ] 编写 `Dockerfile` / `docker-compose` 发布文件
  - [ ] 维护 `CHANGELOG.md`
  - [ ] 准备首个 GitHub Release 内容
  - [ ] 补齐截图、License、免责声明、示例配置等开源发布最小材料
