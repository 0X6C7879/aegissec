---
when: Need custom Semgrep rule authoring guidance, metavariable usage, or taint-mode reminders.
topics:
  - rule-writing
  - metavariable
  - taint-mode
cost_hint: low
---

# Semgrep Rule Writing Notes

Use YAML rules when you need custom pattern detection, metavariable capture, or a lightweight taint mode scan.

- Start with `pattern` or `patterns` for exact and combined matches.
- Use `metavariable-regex` when you need to constrain captured values.
- Prefer `pattern-not` or `pattern-not-inside` to suppress safe variants.
- For taint mode, define source-like inputs, propagation, and sink-like calls explicitly.
