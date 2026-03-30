---
name: c2
description: Use when setting up or selecting C2 frameworks for AD operations, especially Metasploit, msfconsole, msfvenom, Sliver, Havoc, Mythic, payload staging, listener design, and operator infrastructure tradeoffs.
---

# C2

Use this skill when you need an operator-managed command-and-control layer rather than a one-off shell.

## Quick Start

1. Read `references/c2.md`.
2. Choose the framework based on OPSEC, staging, and team workflow constraints.
3. Match payload choice to the target environment and existing bypass strategy.
4. Use script helpers in `scripts/` for payload/listener generation:
   - `msfvenom_gen.py` - msfvenom command and handler block generator
   - `sliver_setup.py` - Sliver server/listener/implant command helper
   - `msf_handler.py` - Metasploit multi/handler `.rc` generator
   - `redirector_setup.py` - socat and iptables redirector command builder
   - `modern_c2_setup.py` - Havoc and Mythic listener/config generator

## Selection Rules

- Prefer framework choice based on operator need, not habit.
- Keep listener, payload, and evasion decisions documented together.
- Treat Metasploit as required coverage, not the only option.

## Output Discipline

For every C2 setup, report:

- framework chosen and why it fits the operation
- listener configuration and bind / callback endpoints
- payload generated and staging approach
- evasion dependency or delivery precondition
- operator workflow rationale (single host / multi-host / long dwell / hands-on keyboard)
- next action after infrastructure goes live

Keep listener and payload notes together so handoff and cleanup stay consistent.

## When To Switch

Switch away from C2 when:

- Infrastructure is operational and the next step is host execution or operator action
- Payload delivery is blocked by endpoint controls
- Engagement goals only need a one-off execution path instead of a framework

Switch to:
- `bypass` when endpoint protections block staging, payload launch, or beacon traffic
- `movement` when C2 enables host-to-host expansion
- `privesc` when the next blocker is local privilege on an accessed host

## References

- `references/c2.md` - C2 framework comparison and setup guidance
- `../shared/references/tool-matrix.md` - supporting tooling and fallbacks
- `../shared/references/output-conventions.md` - infrastructure and evidence discipline
