# 示例配置包

该目录收拢模块 E 交付所需的最小示例配置，避免新协作者在多个文档里来回查找。

## 文件说明

- `.env.local.example`：本地覆盖配置示例，适合复制为仓库根目录 `.env.local`。
- `opencode.json`：用于演示 MCP 导入的最小 OpenCode 配置片段。
- `project-settings.json`：项目级默认工作流与 Runtime 配置示例。

## 推荐使用方式

1. 复制 `.env.example` 为 `.env`。
2. 按需参考本目录中的 `.env.local.example` 叠加本机特有配置。
3. 需要演示 MCP 导入时，可将 `opencode.json` 合并到仓库根或用户级 OpenCode 配置。
4. 需要给项目设置默认工作流时，可参考 `project-settings.json` 的字段结构。
