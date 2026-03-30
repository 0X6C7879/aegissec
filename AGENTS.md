# AGENTS.md
Guide for agentic coding agents working in `D:\AI\aegissec`.

## Mission
- `aegissec` is a local-first defensive security research workbench for authorized environments.
- Optimize for reproducible validation, attack-path analysis, evidence capture, and reporting.
- Prioritize these scenario families:
  - SRC automation and mainstream vulnerability discovery
  - Representative CVE, cloud security, and AI infrastructure validation
  - Multi-step analysis in layered network and OA-style environments
  - Baseline Active Directory simulation
- Do not frame the product as a generalized offensive platform.

## Rule Files
- Checked and not found:
  - `.cursorrules`
  - `.cursor/rules/`
  - `.github/copilot-instructions.md`
  - `D:\AGENTS.md`
  - `D:\AI\AGENTS.md`
- Treat this file as the primary agent instruction source.

## Repository Layout
```text
apps/
  api/        FastAPI + SQLModel backend
  web/        React + TypeScript + Vite frontend
config/       Project configuration root
docker/kali/  Kali image build context
docs/         Product, architecture, and planning docs
scripts/      Root development and validation helpers
```

## Standard Workflow
- Run commands from the repo root unless a section says otherwise.
- Prefer `python scripts/dev.py` for local startup.
- Prefer `python scripts/check.py` for full verification.
- After each completed tracked task, sync `TODO.md` immediately.
- Verify behavior against the running app or API before claiming a fix works.

### Local Development
```bash
python scripts/dev.py
```
- Runs `uv sync --all-extras --dev` in `apps/api`.
- Runs `pnpm install` in `apps/web`.
- Starts FastAPI on `127.0.0.1:8000`.
- Starts Vite on `127.0.0.1:5173`.

### Full Validation
```bash
python scripts/check.py
```
- Backend: `uv sync`, `ruff`, `black --check`, `mypy`, `pytest`
- Frontend: `pnpm install`, `pnpm lint`, `pnpm exec tsc -b`, `pnpm build`

## Backend Commands (`apps/api`)
```bash
uv sync --all-extras --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
uv run ruff check .
uv run black --check .
uv run mypy app tests
uv run pytest
```

### Run a Single Backend Test
```bash
uv run pytest tests/test_health.py
uv run pytest tests/test_health.py::test_health_endpoint_returns_scaffold_status
uv run pytest tests/test_sessions.py::test_chat_can_auto_call_runtime_tools
uv run pytest tests/test_runtime.py::test_runtime_start_execute_status_and_stop
uv run pytest -k runtime
```
- Prefer file or file+function targeting for narrow validation.
- When debugging websocket/session issues, start with `tests/test_sessions.py`.

## Frontend Commands (`apps/web`)
```bash
pnpm install
pnpm dev --host 127.0.0.1 --port 5173
pnpm lint
pnpm exec tsc -b
pnpm build
pnpm preview
```

### Targeted Frontend Checks
```bash
pnpm exec eslint src/components/ConversationFeed.tsx
pnpm exec eslint src/components/ConversationWorkbench.tsx
pnpm exec eslint src/components/WorkbenchComposer.tsx
pnpm exec tsc --noEmit
```
- There is no frontend unit test runner yet.
- For substantial frontend work, run at least `pnpm lint` and `pnpm build`.

## Environment Variables
- Canonical sample file: `D:\AI\aegissec\.env.example`
- Backend vars use the `AEGISSEC_` prefix; frontend vars use the `VITE_` prefix.
- Important current keys:
  - `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_DEFAULT_MODEL`
  - `AEGISSEC_KALI_IMAGE`, `AEGISSEC_RUNTIME_CONTAINER_NAME`
  - `AEGISSEC_RUNTIME_WORKSPACE_CONTAINER_PATH`, `AEGISSEC_RUNTIME_DEFAULT_TIMEOUT_SECONDS`
  - `AEGISSEC_RUNTIME_RECENT_RUNS_LIMIT`, `AEGISSEC_RUNTIME_RECENT_ARTIFACTS_LIMIT`
  - `AEGISSEC_FRONTEND_ORIGIN`

## Backend Style Guide
Source of truth: `apps/api/pyproject.toml` and existing `apps/api/app` files.

- Python 3.12.
- Black line length: 100.
- Ruff rules enabled: `E`, `F`, `I`, `UP`.
- MyPy is `strict`; add explicit types rather than relying on inference at public boundaries.
- Use `from __future__ import annotations` where already established in the file.
- Prefer absolute imports such as `from app.core.settings import get_settings`.
- Order imports as: standard library, third-party, local application imports.
- Naming: `snake_case` for modules/functions/variables, `PascalCase` for classes/models, `SCREAMING_SNAKE_CASE` for constants.
- Keep route modules thin; push business logic into services and repositories.
- Prefer typed request/response models over loose dictionaries.
- Reuse `Settings` / `get_settings()` instead of ad hoc env lookups.
- Raise explicit `HTTPException` values for user-visible failures.
- Do not swallow exceptions silently.
- Close or release long-lived resources deliberately in websocket/background flows.

## Backend Testing Notes
- Tests live in `apps/api/tests`.
- Use `test_*.py` filenames and descriptive `test_...` function names.
- Preserve dependency overrides in `apps/api/tests/conftest.py` for Docker and LLM isolation.
- When changing chat or runtime behavior, update both route tests and runtime-oriented tests.
- When changing session/websocket behavior, update `tests/test_sessions.py`.

## Frontend Style Guide
Source of truth: `apps/web/tsconfig.app.json`, `apps/web/eslint.config.js`, and current `apps/web/src` code.

- TypeScript is `strict`; do not weaken compiler settings.
- UI source of truth: `docs/04_UI设计规范.md`; follow it before adding or reshaping frontend surfaces.
- Use React function components only.
- Use double quotes consistently.
- Naming: `PascalCase` for components/types, `camelCase` for variables/helpers/hooks/setters, descriptive CSS class names.
- Type props, state, mutation payloads, and API responses precisely.
- Keep derived data in `useMemo` only when it improves clarity or avoids repeated recomputation.
- Keep side effects in `useEffect`.
- Use `AbortController` for fetch cleanup.
- Check `response.ok` before trusting payloads.
- Narrow unknown errors with `error instanceof Error`.
- Plain CSS is the styling system; prefer targeted end-of-file overrides over broad rewrites.
- Keep the UI minimal, Chinese-first, and preserve the rule that only the message area scrolls while the composer remains anchored.

## Runtime, Docker, and Docs
- Kali image source is `docker/kali/Dockerfile`.
- Runtime command execution currently uses `/bin/zsh` inside the container.
- The image should include `kali-linux-default` to avoid missing-tool failures.
- Validate runtime changes with backend tests and, when needed, a real image build.
- Planning docs live in `docs/00_个人开源版架构设计.md`, `docs/01_需求文档_PRD.md`, `docs/02_功能实现文档.md`, and `docs/03_开发计划文档.md`.
- `README.md` may lag behind implementation; verify against code and `TODO.md`.
- `TODO.md` is an active execution tracker, not planning history only.

## Agent Checklist Before Finishing
- Run the smallest relevant checks for changed files.
- Backend: prefer targeted `pytest` + `ruff` + `mypy`.
- Frontend: prefer `pnpm lint` + `pnpm build`.
- Update `TODO.md` only for work that is actually complete and verified.
- Reference exact file paths and commands in your summary.

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any shell command containing `curl` or `wget` will be intercepted and blocked by the context-mode plugin. Do NOT retry.
Instead use:
- `context-mode_ctx_fetch_and_index(url, source)` to fetch and index web pages
- `context-mode_ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any shell command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` will be intercepted and blocked. Do NOT retry with shell.
Instead use:
- `context-mode_ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### Direct web fetching — BLOCKED
Do NOT use any direct URL fetching tool. Use the sandbox equivalent.
Instead use:
- `context-mode_ctx_fetch_and_index(url, source)` then `context-mode_ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Shell (>20 lines output)
Shell is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `context-mode_ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `context-mode_ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### File reading (for analysis)
If you are reading a file to **edit** it → reading is correct (edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `context-mode_ctx_execute_file(path, language, code)` instead. Only your printed summary enters context.

### grep / search (large results)
Search results can flood context. Use `context-mode_ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `context-mode_ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `context-mode_ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `context-mode_ctx_execute(language, code)` | `context-mode_ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `context-mode_ctx_fetch_and_index(url, source)` then `context-mode_ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `context-mode_ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `upgrade` MCP tool, run the returned shell command, display as checklist |
