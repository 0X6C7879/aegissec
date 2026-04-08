## 2026-04-08T00:00:00Z Task: bootstrap
Initialized notepad for issue tracking.

## 2026-04-08T23:43:44.5231916+08:00 Task: T1 generation-step merge fix
- The main gotcha is that tool metadata needs conflict resolution by richness, not last-write-wins semantics: nested shell result objects can arrive before a thinner completion/status patch for the same `tool_call_id`.
- Strict TypeScript compatibility is easiest to preserve by keeping the new merge helpers inside `sessionUtils.ts` and limiting the richer merge path to `tool` steps only.

## 2026-04-08T23:58:00+08:00 Task: T2 transcript merge semantics
- `SessionDetail` does not declare `generations`, so transcript enrichment must read that runtime context through a guarded record lookup rather than by widening exported types.
- Arrays inside tool metadata (for example `artifacts` or `artifact_paths`) need merge-time combination; otherwise a later thin replay can still drop evidence even when object-level richer-preferred merging is in place.

## 2026-04-09T00:06:02.4023236+08:00 Task: T3 duplicate tool_result pairing hardening
- The remaining UI-side gotcha is not metadata discovery but duplicate pairing stability: a later richer `tool_result` replay can improve shell content for the same `tool_call_id`, but the paired card should keep the original result segment identity/order so the transcript does not churn while replay data catches up.
- This fallback hardening needs to stay local to `ConversationFeed.tsx`; re-implementing persistence semantics here would duplicate T1/T2 and increase regression risk.

## 2026-04-09T00:17:00+08:00 Task: T4 sessionUtils regression coverage
- A test that expects generation-step enrichment on a brand-new assistant message will miss the real merge path: `mergeSessionMessage()` applies the merged transcript when reconciling an existing stored message, so enrichment fixtures need that persisted message context to exercise the intended behavior.
- Nested array fields inside transcript metadata can make broad `toMatchObject()` assertions noisy; the safer regression style here is to assert the semantic merge outcome plus the specific nested shell fields that must survive replay.

## 2026-04-09T00:16:30+08:00 Task: T5 ConversationFeed persistence regressions
- Testing Library text matchers are brittle for `<pre>` terminal content because the command prompt span and newline text nodes split the visible output; terminal assertions are more stable when they read `.assistant-terminal-output.textContent` for stdout/stderr persistence checks.
