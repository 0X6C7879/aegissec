---
name: pattt-readme-loader
description: README-first PayloadsAllTheThings loader that resolves real vendored docs before suggesting traceable candidates.
user_invocable: false
preferred_stage: verification
context_strategy: focused
semantic_family: payloadsallthethings
semantic_domain: offensive-knowledge
semantic_task_mode: retrieval
semantic_tags:
  - pattt
  - payloadsallthethings
  - readme-first
  - verification
when_to_use: Use when a task needs payload, bypass, or exploit guidance from PayloadsAllTheThings and a README-first source load is required.
context_hint: This skill must resolve vendored PATTT docs from disk first; catalog metadata is only for routing.
---
# pattt-readme-loader

This is a thin loader skill for the vendored `PayloadsAllTheThings` corpus.

Rules:
- Call the PATTT resolver before choosing any payloads.
- The resolver must read the real vendored `README.md` or `.md` source files from disk.
- `loaded_docs` returned by the resolver must be included in model context.
- Without `loaded_docs`, PATTT must not be treated as a known source.
- Candidate suggestions must carry `source_path` and `section_title`.
- Default policy is verification-first.
- Bypass suggestions require explicit intent.
- Exploit suggestions require explicit gating.
- Catalog data may route or rank docs, but it must never replace raw source reads.
