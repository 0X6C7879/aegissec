---
name: movement
description: Use when performing AD lateral movement with Impacket, NetExec, Evil-WinRM, WMI, DCOM, SMB exec, PtH, PtT, RDP, or when you have credentials and need host-to-host execution.
---

# Movement

Use this skill after credential acquisition when the main objective is expanding access across Windows or AD hosts.

## Quick Start

1. Read `references/movement.md`.
2. Identify what credential form you have: password, NTLM hash, ticket, or cert-derived access.
3. Pick the quietest execution path that proves or expands access.
4. Use script helpers in `scripts/` for repeatable execution wrappers:
   - `pth_spray.py` - PtH spray via NetExec/CrackMapExec
   - `wmiexec_run.py` - WMI remote command wrapper
   - `evil_winrm_wrap.py` - Evil-WinRM + pre-check command builder
   - `smb_exec.py` - psexec/smbexec/atexec execution wrapper
   - `dcom_exec.py` - DCOM execution wrapper for ShellWindows/MMC20 paths
   - `rdp_connect.py` - xfreerdp command builder with restricted-admin support

## Selection Rules

- Prefer WinRM or WMI over louder service-creation paths when feasible.
- Use Impacket and NetExec for protocol-accurate validation before full execution.
- Record which credential worked on which host and protocol.

## Output Discipline

For every lateral movement attempt, report:

- credential form used (password / NTLM / ticket / cert)
- target host and identity context
- execution method (WinRM / WMI / SMB exec / DCOM / RDP)
- command run or action performed
- access level gained (standard user / local admin / SYSTEM)
- next expansion target or objective

Keep validated credentials organized by host and access level.

## When To Switch

Switch away from lateral movement when:

- Need local privilege escalation on newly accessed host
- Achieved domain admin or equivalent and need durable persistence
- Exhausted accessible targets and need new reconnaissance scope

Switch to:
- `privesc` for local escalation when you need SYSTEM or higher integrity
- `persistence` after gaining privileged domain access
- `adscan` when you need fresh enumeration with newly obtained credentials

## References

- `references/movement.md` - movement decision trees and command examples
- `../shared/references/tool-matrix.md` - fallback tools and substitutions
- `../shared/references/output-conventions.md` - credential and evidence logging
