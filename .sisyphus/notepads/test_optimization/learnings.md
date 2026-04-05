# Test Optimization Learnings: test_sessions.py

## Test Setup Dependency Map

### Core Components Setup Flow

1. **Session Setup**: Tests create sessions via `/api/sessions` POST, then interact with lifecycle endpoints (pause, resume, cancel, restore)
2. **Websocket Setup**: Uses `client.websocket_connect(f"/api/sessions/{session_id}/events")` for real-time event streaming
3. **Chat Setup**: Posts to `/api/sessions/{session_id}/chat` with content/attachments, handles blocking/non-blocking responses
4. **Runtime Setup**: Executes commands via `/api/runtime/execute` with session_id, command, timeout, artifacts

### Dependency Chain: Test Cases → Fixtures → Overrides → Services/Routes

```text
test_chat_persists_messages_and_attachments(client)
├── client (fixture)
│   ├── test_settings (fixture)
│   ├── runtime_backend (fixture: FakeRuntimeBackend)
│   ├── app.dependency_overrides
│   │   ├── get_db_session → override_db_session (creates Session(engine))
│   │   ├── get_websocket_db_session → override_websocket_db_session (creates Session(engine))
│   │   ├── get_event_broker → SessionEventBroker with persistence
│   │   ├── get_chat_runtime → FakeChatRuntime
│   │   ├── get_settings → test_settings
│   │   └── get_runtime_backend → runtime_backend
│   └── TestClient(app) with fresh SQLite DB
└── /api/sessions/{id}/chat → routes_chat.py → chat_runtime.generate_reply()

test_websocket_streams_session_events(client)
├── client (fixture) [same as above]
├── client.websocket_connect("/api/sessions/{id}/events")
│   └── get_websocket_db_session (overridden) → Session(engine)
└── Event streaming via SessionEventBroker

test_session_history_and_artifact_endpoints_support_filters(client)
├── client (fixture) [same as above]
├── /api/runtime/execute → runtime_backend.execute()
│   └── FakeRuntimeBackend._materialize_artifact()
└── /api/sessions/{id}/history & /api/sessions/{id}/artifacts
```

### Key Service Interactions

- **Chat Runtime**: `FakeChatRuntime.generate_reply()` simulates LLM responses with streaming callbacks.
- **Runtime Backend**: `FakeRuntimeBackend.execute()` queues results, materializes artifacts to workspace dir.
- **Event Broker**: `SessionEventBroker` persists events to DB for websocket replay.
- **DB Sessions**: Fresh SQLite connections per test, with request logging.

## Expensive Initialization Analysis

### Repeated Costly Operations

1. **Database Setup (Per Test)**:
   - `create_engine(database_url, connect_args={"check_same_thread": False})`
   - `SQLModel.metadata.create_all(engine)` - Creates all tables.
   - **Cost**: ~50-100ms per test, repeated across 20+ session tests.

2. **Event Broker Persistence**:
   - `SessionEventBroker().configure_persistence(lambda: Session(engine))`
   - **Cost**: DB session creation + event table setup per test.

3. **Runtime Workspace Creation**:
   - `FakeRuntimeBackend.__init__()` creates `Path(settings.runtime_workspace_dir).resolve().mkdir(parents=True, exist_ok=True)`
   - **Cost**: Filesystem mkdir operation per test.

4. **App State Overrides**:
   - `app.dependency_overrides.clear()` at end, but full override setup each time.
   - **Cost**: Dependency injection overhead.

### Optimization Opportunities

1. **Cache SQLite Engine (High Impact)**
   - **Current**: Fresh engine + table creation per test.
   - **Suggestion**: Use `scope="session"` fixture for shared in-memory SQLite (or an optimized setup/teardown strategy).
   - **Benefit**: Significant reduction in DB setup time.

2. **Stub Runtime Backend Workspace (Medium Impact)**
   - **Current**: Creates real directories via `mkdir(parents=True, exist_ok=True)`.
   - **Suggestion**: Mock `Path.mkdir()` or use in-memory filesystem in `FakeRuntimeBackend`.
   - **Benefit**: Eliminates unnecessary filesystem I/O in tests.

3. **Reuse Event Broker (Low Impact)**
   - **Current**: New broker instance per test.
   - **Suggestion**: Singleton broker with test-scoped persistence.
   - **Benefit**: Reduces object creation overhead.

4. **Lazy Artifact Materialization (Low Impact)**
   - **Current**: `_materialize_artifact()` writes files immediately.
   - **Suggestion**: Defer writes or use memory buffers for non-filesystem tests.

5. **Parallel Test Execution (High Impact)**
   - **Suggestion**: Configure pytest-xdist for parallel runs once test isolation is solid.