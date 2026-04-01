# 前后端 Schema 共享策略

- 后端以 `apps/api/app/main.py` 暴露的 OpenAPI 作为契约源。
- 统一导出命令：`uv run python ..\..\scripts\export_api_schema.py`（在 `apps/api` 目录运行）。
- 导出结果写入：`apps/web/src/generated/api-schema.json`。
- 前端运行时仍通过 `apps/web/src/lib/api.ts` 做轻量解包与兼容，但新增接口和字段应先更新后端 Pydantic/SQLModel schema，再重新导出契约文件。
- 交付时至少验证一次导出命令可运行，并确保前端 `pnpm build` 通过。
