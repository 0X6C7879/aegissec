---
when: Need custom query authoring hints, dataflow modules, or path-problem structure.
topics:
  - query-writing
  - dataflow
  - taint-tracking
cost_hint: medium
---

# CodeQL Query Writing Notes

Use CodeQL when you need interprocedural data flow and path explanations.

- Start with the language pack imports.
- Use `DataFlow::ConfigSig` or taint tracking helpers for source-to-sink logic.
- Prefer path-problem queries when you need a trace, not just a match.
