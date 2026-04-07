# Refactor Plan: Transcript-First Event System for aegissec

## 1. Project Goal
Transition `aegissec` to a transcript-first, event-sourced orchestration system to ensure high-concurrency reliability, UI/API consistency, and robust async verification.

## 2. Technical Invariants
- **Backend-Signed Sequencing**: Every event has a global monotonic `gsid`.
- **Pre-Execution Persistence**: Commands only run after the triggering event is stored.
- **Deduplicated Sinks**: Frontend and workers use `correlation_id` + `gsid` to prevent double-processing.

## 3. Implementation Steps

### Step 1: Schema Definition & DB Migrations
- Define `TranscriptEvent` SQLModel: `gsid` (BIGINT), `correlation_id` (UUID), `timestamp`, `event_type`, `payload`.
- Create migration to add `transcript` table.
- **Verification**: `uv run pytest tests/test_schema.py`

### Step 2: Reliable WebSocket Mailbox
- Implement `TranscriptManager` for reliable event delivery.
- Add "Backfill" logic: replay missed events based on client's `last_received_gsid`.
- **Verification**: WebSocket integration test with simulated drop/reconnect.

### Step 3: Idempotent Frontend State (React)
- Refactor `ConversationFeed` to use a Map-based event reducer.
- Use `(correlation_id + gsid)` as React keys.
- **Verification**: `pnpm build` + manual check of message stability during rapid updates.

### Step 4: TDD for Async Event Pipelines
- Implement "Given-When-Then" stream tests.
- Simulate "Race Condition Hazards": Delayed tool execution, out-of-order `ToolFinished` events.
- **Verification**: `uv run pytest tests/test_event_hazards.py`

## 4. Risks & Mitigations
- **Divergence**: Mitigation: Share the `EventTransformer` logic between live-stream and historical-fetch.
- **Stale Content**: Mitigation: Implement "Read-Your-Writes" by waiting for the stream to reach `max_gsid`.

## 5. Sources
- [Significant-Gravitas/AutoGPT](https://github.com/Significant-Gravitas/AutoGPT)
- [EventSourcingDB Consistency Guarantees](https://docs.eventsourcingdb.io/)
- [WebSocket.org Production Best Practices](https://websocket.org/)
