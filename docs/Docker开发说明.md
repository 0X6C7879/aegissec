# Docker 开发说明

## 1. 何时使用 Compose

优先级建议：

1. **日常开发**：`python scripts/dev.py`
2. **需要一键拉起完整环境或做交付演示**：`docker compose up --build`

`scripts/dev.py` 更适合本地调试；`docker-compose.yml` 更适合模块 E 验收、截图、演示和新协作者快速试跑。

## 2. Compose 提供的服务

- `api`：FastAPI 开发服务，监听 `8000`
- `web`：Vite 开发服务，监听 `5173`
- `kali-runtime`：预置 `kali-linux-default` 的 Runtime 容器

## 3. 启动方式

```bash
cp .env.example .env
docker compose up --build
```

启动完成后：

- API: `http://127.0.0.1:8000`
- Docs: `http://127.0.0.1:8000/docs`
- Web: `http://127.0.0.1:5173`

## 4. 关键说明

- `api` 服务会挂载仓库根目录，直接使用当前工作区代码。
- `kali-runtime` 容器名固定为 `aegissec-kali-runtime`，与默认环境变量保持一致。
- `api` 服务挂载 Docker socket，用于 Runtime 服务通过 Docker SDK 检查和控制 `kali-runtime` 容器。
- 如果本机 Docker 环境不允许 socket 挂载，优先退回 `python scripts/dev.py` 路径。

## 5. 常见问题

### 5.1 Web 能打开但 API 失败

先访问 `http://127.0.0.1:8000/api/health`，再检查：

- `api` 服务日志
- `.env` 中端口是否被改动
- `VITE_API_BASE_URL` 是否仍指向 `127.0.0.1:8000`

### 5.2 Runtime 健康状态为 degraded

这是允许的降级状态，表示容器尚未启动。可在 Runtime 页面或通过 API 调用 `/api/runtime/start` 完成拉起。

### 5.3 前端热更新慢

Compose 更偏向演示/验收。如果需要更高频迭代，使用 `python scripts/dev.py`。
