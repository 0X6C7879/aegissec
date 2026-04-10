---
when: Need scan-triage guidance, output handling, or SARIF/JSON reporting choices.
topics:
  - triage
  - sarif
  - output
cost_hint: low
---

# Semgrep Triage Notes

Prefer `--sarif` when findings must feed code scanning, and `--json` when post-processing with scripts.

- Use `--config auto` for a first pass.
- Switch to curated packs for focused follow-up.
- Keep false-positive review separate from custom-rule authoring.
