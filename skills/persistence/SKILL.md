---
name: persistence
description: Use when establishing AD or Windows persistence with Golden Ticket, Silver Ticket, DCSync-derived material, AD CS certificate persistence, scheduled tasks, or WMI event subscriptions after gaining privileged access.
---

# Persistence

Use this skill after obtaining meaningful privileges and needing durable access that survives credential churn or host restarts.

## Quick Start

1. Read `references/persistence.md`.
2. Separate host-local persistence from domain-level persistence.
3. Prefer the least noisy technique that matches the privilege level you actually hold.
4. Use script helpers in `scripts/` for repeatable persistence operations:
   - `golden_ticket.py` - Golden Ticket generation wrapper
   - `silver_ticket.py` - Silver Ticket generation wrapper
   - `dcsync.py` - secretsdump DCSync wrapper
   - `cert_persist.py` - Certipy find/request/auth wrapper
   - `machine_account.py` - addcomputer wrapper for machine-account persistence paths
   - `host_persist.py` - registry Run key and scheduled-task command builder

## Selection Rules

- Domain techniques require stronger justification and cleaner evidence trails.
- Record prerequisites, detection risk, and cleanup path before applying persistence.
- Keep at least one recovery path documented separately from the active persistence mechanism.

## Output Discipline

For every persistence mechanism, report:

- persistence type (Golden Ticket / Silver Ticket / scheduled task / WMI event / AD CS cert / etc)
- prerequisites verified (privilege level / credential material / target access)
- detection risk assessment (high / medium / low visibility)
- cleanup procedure documented
- recovery path (how to regain access if this mechanism fails)

Keep persistence artifacts and credentials in organized evidence tree.

## When To Switch

Switch away from persistence when:

- Durable access successfully established
- Engagement scope complete or shifting to new domain/segment
- Persistence mechanism detected and need bypass or alternate technique

Switch to:
- `bypass` if persistence mechanism gets blocked by AV/EDR
- `adscan` when using new persistence-derived credentials to expand enumeration scope
- Core engagement workflow when persistence objectives complete

## References

- `references/persistence.md` - domain and host persistence techniques
- `../shared/references/output-conventions.md` - evidence and credential handling
