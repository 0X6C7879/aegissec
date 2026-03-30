---
name: adpwn
description: Skill bundle for authorized Active Directory pentest orchestration covering recon, tunneling, lateral movement, local privilege escalation, persistence, bypass, and C2 workflow selection.
---

# adpwn

Use this bundle when the task is an authorized Active Directory or Windows domain assessment and you need to choose the right workflow-specific skill instead of improvising a generic sequence.

## Selection Rule

- Use `adscan` for reconnaissance, validation, BloodHound collection, AD CS checks, SMB share analysis, relay preparation, and credential expansion.
- Use `tunnel` when internal reachability or segmentation is the blocker.
- Use `movement` when you already have valid credentials and need host-to-host execution.
- Use `privesc` when you need local administrator or SYSTEM on a compromised Windows host.
- Use `persistence` after you gain meaningful host or domain privileges and need a durable re-entry path.
- Use `bypass` when Defender or EDR blocks the tools or payloads you need.
- Use `c2` when you need staged payloads, listeners, redirectors, or framework tradeoff guidance.

## Output Discipline

- State which skill you selected and why it matches the current foothold.
- Keep commands, findings, and evidence grouped by phase.
- Prefer the smallest workflow that answers the current question.

## Safety Boundary

This bundle is for authorized security testing, lab work, research, and operator-assisted orchestration in environments you are explicitly permitted to assess. Do not use it for unauthorized access or illegal activity.
