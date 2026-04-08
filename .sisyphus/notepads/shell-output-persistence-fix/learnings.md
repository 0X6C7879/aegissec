## 2026-04-08T00:00:00Z Task: bootstrap
Initialized notepad for cumulative learnings.

## 2026-04-08T23:43:44.5231916+08:00 Task: T1 generation-step merge fix
- `mergeGenerationStepList()` in `apps/web/src/lib/sessionUtils.ts` must not use a plain `incomingStep.metadata ?? existingStep.metadata` replacement for tool steps, because later thin live updates can erase richer shell payloads already stored under `output`, `execution`, `payload`, `data`, or `result`.
- A recursive richer-preferred merge keeps backward compatibility for non-tool steps while preserving nested shell metadata and still allowing same-richness lifecycle fields like `status` to advance.

## 2026-04-08T23:58:00+08:00 Task: T2 transcript merge semantics
- `mergeSessionMessage()` also needs semantic transcript reconciliation for tool segments: exact `id` matching is not enough because persisted transcript replays can represent the same tool call under different segment ids while sharing `kind + tool_call_id`.
- The persistence-safe path is: merge existing transcript + incoming transcript + any matching generation tool steps, then resolve conflicts with richer-preferred field selection and recursive metadata merging so thin persisted tool results cannot erase nested shell output fields.

## 2026-04-09T00:06:02.4023236+08:00 Task: T3 duplicate tool_result pairing hardening
- `buildToolPairs()` still benefits from a UI-side safety net even after persistence fixes: repeated `tool_result` segments with the same `tool_call_id` should merge with richer-preferred semantics while preserving the first paired result segment's stable fields so render pairing/order does not drift.
- The minimal safe change is to keep using `mergeOrPickToolSegment()` for duplicate `tool_result` values, but pass `preserveStableFields` once a result is already paired; this lets richer shell payload metadata win without re-keying the existing transcript block.

## 2026-04-09T00:17:00+08:00 Task: T4 sessionUtils regression coverage
- `mergeSessionMessage()` reconciliation is best validated with transcript fixtures that distinguish three sources explicitly: existing transcript state, incoming transcript replay, and generation-derived tool-step enrichment; this catches regressions where thin tool-result replays would otherwise erase nested shell payloads.
- For transcript semantics, the stable regression signal is: same `kind + tool_call_id` collapses into one segment even when ids differ, while richer nested shell metadata from generation/live state remains visible on the preserved transcript entry.

## 2026-04-09T00:16:30+08:00 Task: T5 ConversationFeed persistence regressions
- `ConversationFeed.test.tsx` needed a true `rerender()` lifecycle case, not only point-in-time transcript fixtures, to prove that a later persisted thin `tool_result` replay cannot visually downgrade stdout/stderr that previously came from richer generation-step data.
- A separate persisted-message-only regression is useful because it confirms shell stdout/stderr rendering still works when `ConversationFeed` has no active generation assistance and must rely entirely on the final stored assistant transcript.
