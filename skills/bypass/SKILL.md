---
name: bypass
description: Use when AV or EDR blocks execution and you need AMSI bypass, ETW patching, userland unhooking, LOLBins, loader tradeoffs, or BYOVD-aware decision support during authorized AD pentests.
---

# Bypass

Use this skill when the blocker is endpoint protection rather than missing access or routing.

## Quick Start

1. Read `references/bypass.md`.
2. Identify what control is present: Defender only, commercial EDR, or unknown.
3. Pick the least invasive bypass that enables the required action.
4. Use script helpers in `scripts/` for snippet lookup and command construction:
   - `amsi_snippets.py` - AMSI bypass snippet library
   - `etw_patch.py` - ETW patching snippet library
   - `lolbin_exec.py` - LOLBin execution command builder with detection notes

## Selection Rules

- Prefer reducing tool footprint before escalating to heavy bypass techniques.
- Separate evasion guidance from payload selection.
- Log product, symptom, attempted bypass, and resulting execution window.

## Output Discipline

For every bypass attempt, report:

- control detected (Defender / EDR family / unknown)
- symptom observed (blocked binary / AMSI hit / child-process kill / network block)
- bypass technique applied
- execution window gained or lost
- payload or action enabled by the bypass
- next blocker or next objective

Keep failed and successful attempts separated to avoid repeating noisy paths.

## When To Switch

Switch away from bypass when:

- Execution is now possible with the required tool or payload
- The main blocker turns out to be infrastructure or staging rather than endpoint controls
- The bypass path is too noisy for current engagement constraints

Switch to:
- `c2` when bypass enables staged delivery or framework execution
- Core engagement workflow when bypass solves the immediate blocker
- `adscan` or `movement` when you can resume recon or host access with the new execution window

## References

- `references/bypass.md` - AV/EDR bypass guidance and tradeoffs
- `../shared/references/tool-matrix.md` - supporting tools and loader options
