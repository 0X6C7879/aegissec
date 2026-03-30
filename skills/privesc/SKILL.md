---
name: privesc
description: Use when performing Windows local privilege escalation with WinPEAS, Seatbelt, Potato-family techniques, token abuse, service abuse, or when you have a low-priv foothold and need admin or SYSTEM.
---

# PrivEsc

Use this skill for host-local privilege escalation on Windows after a foothold is established.

## Quick Start

1. Read `references/privesc.md`.
2. Start with low-noise enumeration before exploit execution.
3. Move from enumeration to the smallest viable escalation path.
4. Use script helpers in `scripts/` for common local escalation workflows:
   - `winpeas_runner.py` - WinPEAS execution and finding extraction
   - `potato_launcher.py` - Potato-family command and requirement builder
   - `service_misconfig.py` - service ACL/unquoted-path check command generator
   - `cred_harvest.py` - lsassy/nanodump/SAM credential harvest wrapper
   - `always_install.py` - AlwaysInstallElevated MSI and msiexec command builder

## Selection Rules

- Run enumeration first; do not jump straight to exploit binaries.
- Prefer abuse of existing privileges, tokens, and service misconfigurations before kernel or noisy exploit paths.
- Log the precondition, exploit path, and resulting integrity level.

## Output Discipline

For every escalation attempt, report:

- enumeration findings (WinPEAS / Seatbelt / manual checks)
- exploit path chosen (token abuse / service misconfiguration / kernel exploit / Potato-family / etc)
- preconditions met (required privilege / group membership / service state)
- resulting integrity level (Medium → High / High → SYSTEM)
- next objective (credential extraction / persistence / further movement)

Keep enumeration results for audit trail and repeat access.

## When To Switch

Switch away from privilege escalation when:

- SYSTEM or administrative access achieved on current host
- Domain credentials or secrets harvested enabling lateral movement
- Escalation path not viable and need alternate approach

Switch to:
- `movement` when you have harvested domain credentials and want to expand laterally
- `persistence` when you have SYSTEM or domain admin and need durable access
- `adscan` when newly obtained credentials enable fresh domain enumeration

## References

- `references/privesc.md` - enumeration and escalation playbook
- `../shared/references/output-conventions.md` - evidence layout and host tracking
