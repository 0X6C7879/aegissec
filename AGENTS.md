# AGENTS.md
Guide for agentic coding agents working in `D:\AI\aegissec`.

## Mission
- `aegissec` is a local-first defensive security research workbench for authorized environments.
- Optimize for reproducible validation, attack-path analysis, evidence capture, and reporting.
- Prioritize SRC automation, representative CVE/cloud/AI validation, layered-network workflows, and baseline AD simulation.

## Rule Files
- Checked and not found:
  - `.cursorrules`
  - `.cursor/rules/`
  - `.github/copilot-instructions.md`
- Treat this file as the primary agent instruction source for this repository.

## Repository Layout
```text
apps/
  api/        FastAPI + SQLModel backend
  web/        React + TypeScript + Vite frontend
config/       Project configuration
docker/kali/  Kali image build context
docs/         Product and implementation docs
scripts/      Dev and verification helpers
TODO.md       Active execution tracker
```

## Working Rules
- Run commands from the repo root unless a section says otherwise.
- Prefer the provided helper scripts over ad hoc startup/check flows.
- Make the smallest correct change; do not redesign unrelated areas.
- Verify behavior before claiming a fix works.
- Update `TODO.md` only for work that is actually complete and verified.

## Primary Commands

### Local Development
```bash
python scripts/dev.py
```
- Syncs backend deps with `uv sync --all-extras --dev`.
- Installs frontend deps with `pnpm install`.
- Starts FastAPI on `127.0.0.1:8000`.
- Starts Vite on `127.0.0.1:5173`.

### Full Verification
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
- Start with `tests/test_sessions.py` for session/websocket/chat regressions.
- Start with `tests/test_runtime.py` or `tests/test_chat_runtime.py` for runtime execution changes.

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
pnpm exec tsc --noEmit
```
- There is no dedicated frontend unit-test runner at the moment.
- For meaningful frontend changes, run at least `pnpm lint` and `pnpm build`.

## Platform Notes
- Backend requires Python `>=3.12`.
- Frontend package manager is `pnpm@10.15.1`.
- Canonical env sample: `.env.example`.

## Backend Style Guide
Source of truth: `apps/api/pyproject.toml` and existing files under `apps/api/app`.

- Use Python 3.12 features deliberately.
- Black and Ruff line length is `100`.
- Ruff lint rules enabled: `E`, `F`, `I`, `UP`.
- MyPy runs in `strict`; add explicit types at public boundaries.
- Use `from __future__ import annotations` where the file already uses it or where new typing benefits from it.
- Prefer absolute imports like `from app.core.settings import get_settings`.
- Import order: standard library, third-party, local application imports.
- Keep modules, functions, and variables `snake_case`.
- Keep classes, Pydantic/SQLModel types, and enums `PascalCase`.
- Keep constants `SCREAMING_SNAKE_CASE`.
- Keep route modules thin; move business logic into services/repositories/helpers.
- Prefer typed request/response models over loose `dict[str, Any]` payloads.
- Reuse `Settings` / `get_settings()` instead of ad hoc environment lookups.
- Raise explicit `HTTPException` values for user-facing API failures.
- Do not swallow exceptions silently.
- Close or release long-lived resources in websocket/background/runtime flows.
- Prefer small focused helpers over deeply nested route handlers.

## Backend Testing Notes
- Tests live in `apps/api/tests` and use `test_*.py` naming.
- Keep new test names descriptive and behavior-oriented.
- Preserve dependency overrides in `apps/api/tests/conftest.py`.
- When changing session/chat/runtime behavior, update both route-level and runtime-level coverage.

## Frontend Style Guide
Source of truth: `apps/web/tsconfig.app.json`, `apps/web/eslint.config.js`, and existing files under `apps/web/src`.

- TypeScript is `strict`; do not weaken type settings.
- Use React function components only.
- Use double quotes consistently.
- Keep component/type names `PascalCase`.
- Keep variables, helpers, hooks, and setters `camelCase`.
- Keep CSS class names descriptive and feature-scoped.
- Type props, API responses, state, and mutation payloads precisely.
- Check `response.ok` before trusting fetch results.
- Narrow unknown failures with `error instanceof Error`.
- Use `AbortController` for cancellable fetches/effects.
- Keep side effects in `useEffect`.
- Use `useMemo` only when it improves clarity or avoids repeated expensive work.
- Prefer plain CSS and targeted end-of-file overrides over broad rewrites.
- Preserve the Chinese-first, minimal UI language already present in the app.
- For conversation pages, keep the message area scrollable while the composer remains anchored.
- Follow `docs/04_UI设计规范.md` before reshaping frontend surfaces.

## Runtime, Docker, and Docs
- Kali image source: `docker/kali/Dockerfile`.
- Runtime command execution currently uses `/bin/zsh` inside the container.
- The image should include `kali-linux-default` to avoid missing-tool failures.
- Validate runtime/container changes with backend tests and, when needed, a real image build.
- `README.md` may lag behind implementation; verify against code and `TODO.md`.

## Agent Checklist Before Finishing
- Run the smallest relevant checks for the files you changed.
- Backend: prefer targeted `pytest` + `ruff` + `mypy`.
- Frontend: prefer `pnpm lint` + `pnpm build`.
- Confirm the changed behavior in the running API or browser when practical.
- Update `TODO.md` only for completed, verified work.
- In your summary, cite exact file paths and exact commands you ran.

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
