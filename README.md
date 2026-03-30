# aegissec

`aegissec` 是一个面向授权环境的本地优先防御型安全研究工作台，围绕可复现的漏洞验证、攻击路径分析与证据驱动报告建设。项目的最高优先级是逐步服务 SRC 自动化众测与主流漏洞发现、典型 CVE / 云安全 / AI 基础设施漏洞实测、多层网络与 OA 环境推演，以及基础域渗透模拟等核心场景。当前仓库仍处于 Week 1 工程脚手架阶段，现有代码只提供后续能力演进所需的最小运行底座。

## 使命与最高约束

- 以授权环境下的防御性研究、验证、建模、留痕与报告为核心目标
- 优先支持 SRC 场景中的自动化众测与主流漏洞发现
- 优先支持典型 CVE、云安全与 AI 基础设施问题的可复现验证
- 优先支持多层网络 / OA / 基础域环境中的多步路径推演、证据串联与结果输出
- 后续开发取舍以贴近这些场景为第一优先级，而不是扩张为泛化攻击平台

## 当前状态

- 已提供 `apps/api`：FastAPI + Pydantic Settings + SQLModel 依赖基线，含 `/health` 接口与 pytest smoke test
- 已提供 `apps/web`：React + TypeScript + Vite 最小前端，可展示脚手架状态并请求后端健康检查
- 已提供 `docker/kali/Dockerfile`：Kali 基础镜像构建入口
- 已提供根目录 `.env.example` 与根脚本：`scripts/dev.py`、`scripts/check.py`
- 本阶段严格只覆盖工程初始化，不包含 Session、Chat、Runtime API、Skill/MCP 业务逻辑、Workflow、Graph、Report 等 Week 2+ 功能

## 目录

```text
apps/
  api/       FastAPI scaffold
  web/       React + Vite scaffold
config/      Project configuration root
docker/kali/ Kali base image build context
scripts/     Root development helpers
docs/        Planning and product documents
```

## 环境要求

- Python 3.12+
- Node.js 20+
- `uv`
- `pnpm`
- Docker

## 快速开始

1. 复制环境文件

```bash
cp .env.example .env
```

2. 从仓库根目录一键启动本地开发环境

```bash
python scripts/dev.py
```

脚本会自动：

- 在 `apps/api` 执行 `uv sync --all-extras --dev`
- 在 `apps/web` 执行 `pnpm install`
- 启动 FastAPI 开发服务：`http://127.0.0.1:8000/health`
- 启动 Vite 开发服务：`http://127.0.0.1:5173`

## 单独运行

后端：

```bash
cd apps/api
uv sync --all-extras --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端：

```bash
cd apps/web
pnpm install
pnpm dev --host 127.0.0.1 --port 5173
```

Kali 基础镜像：

```bash
docker build -t aegissec-kali:latest ./docker/kali
```

## 基础检查

从仓库根目录运行：

```bash
python scripts/check.py
```

该脚本会执行：

- 后端 `ruff check`
- 后端 `black --check`
- 后端 `mypy`
- 后端 `pytest`
- 前端 `eslint`
- 前端 `tsc -b`
- 前端 `vite build`

## Ubuntu 全新系统一键安装 / 校验 / 启动

如果你已经把仓库源码放到一台全新的 Ubuntu 主机上，可以直接运行：

```bash
bash scripts/bootstrap_ubuntu.sh
```

默认会顺序执行：

- 安装系统依赖（Python、Node.js、pnpm、uv、Docker 等）
- 使用指定脚本安装 Docker：`bash <(wget -qO- https://xuanyuan.cloud/docker.sh)`
- 自动复制 `.env.example` 为 `.env`（若 `.env` 不存在）
- 安装后端 / 前端依赖并构建 `aegissec-kali:latest`
- 运行后端 `ruff`、`black --check`、`mypy`、`pytest`
- 运行前端 `pnpm lint`、`pnpm exec tsc -b`、`pnpm build`
- 后台启动 API 与 Web 开发服务，并等待健康检查通过

也可以拆分执行：

```bash
bash scripts/bootstrap_ubuntu.sh install
bash scripts/bootstrap_ubuntu.sh verify
bash scripts/bootstrap_ubuntu.sh start
bash scripts/bootstrap_ubuntu.sh status
bash scripts/bootstrap_ubuntu.sh stop
```

说明：

- 请使用**普通用户**执行脚本，脚本内部会通过 `sudo` 安装系统依赖
- 当前仓库仍以开发态启动为主，因此脚本启动的是 FastAPI + Vite 开发服务
- 启动日志会写入 `.aegissec/logs/`

## 文档

- `docs/00_个人开源版架构设计.md`
- `docs/01_需求文档_PRD.md`
- `docs/02_功能实现文档.md`
- `docs/03_开发计划文档.md`

## 免责声明

本项目仅适用于授权环境下的防御性研究、验证、模拟推演、证据整理与学习用途。请勿将其用于未授权目标、违法用途或任何脱离授权边界的攻击活动。
