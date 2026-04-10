---
when: Need database creation workflow, build-command reminders, or language-specific setup advice.
topics:
  - database-workflow
  - build-command
  - source-root
cost_hint: medium
---

# CodeQL Database Workflow

Create the database first, then analyze it with the right query pack.

- Use `codeql database create` with `--language` and `--source-root`.
- Supply `--command` for compiled languages that need a full build.
- Keep the database directory stable so repeat analysis is deterministic.
