# shell-output-persistence-fix

## TODOs
- [x] T1: Fix generation-step merge in `apps/web/src/lib/sessionUtils.ts` so rich tool metadata cannot be downgraded.
- [x] T2: Fix transcript/message merge in `apps/web/src/lib/sessionUtils.ts` with semantic tool merge by `kind + tool_call_id`, richer-preferred merge, and enrichment from generation steps.
- [x] T3: Add minimal richer-priority fallback hardening in `apps/web/src/components/ConversationFeed.tsx` for duplicate `tool_result` with same `tool_call_id`.
- [x] T4: Add/update tests in `apps/web/src/lib/sessionUtils.test.ts` for richness-preserving generation/transcript merge and semantic merge across different segment ids.
- [x] T5: Add/update tests in `apps/web/src/components/ConversationFeed.test.tsx` for lifecycle persistence and richer duplicate tool_result selection.
- [x] T6: Run required tests: `sessionUtils.test.ts` and `ConversationFeed.test.tsx`.

## Final Verification Wave
- [ ] F1: Implementation completeness review (all required behaviors covered)
- [ ] F2: Regression and scope review (no unrelated changes)
- [ ] F3: Test verification review (required tests pass)
- [ ] F4: Persistence-root-cause review (fix is merge/persistence-centered, not display-only)
