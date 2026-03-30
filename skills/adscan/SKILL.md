---
name: adscan
description: Use when performing Active Directory pentest orchestration without using ADscan itself, especially for domain enumeration, credential validation, BloodHound collection, AD CS abuse, SMB share analysis, relay/cracking workflows, or when replacing ADscan with underlying tools like NetExec, Impacket, Certipy, BloodHound, kerbrute, Responder, hashcat, SMBMap, or Snaffler.
---

# AD Pentest Orchestration Without ADscan

Recreate the main ADscan workflows by composing the underlying AD toolchain directly. Start from operator intent, detect what tools are available locally, choose the narrowest viable workflow, save evidence as you go, and never call `adscan` itself.

## Core Rule

Treat this skill as an orchestrator, not a giant tool list.

1. Detect available tools first.
2. Pick the workflow stage that matches the user's current foothold.
3. Execute the smallest high-signal sequence.
4. Save commands, outputs, and findings in a predictable workspace.
5. Feed new credentials/findings back into enumeration and path analysis.

## Workflow Decision Tree

1. **Need environment/tool awareness first?**
   - Run `scripts/detect_ad_toolchain.py`
   - Then read `references/tool-matrix.md`
2. **Starting from no credentials / black-box foothold?**
   - Follow `Unauth Enumeration` below
3. **Have one or more credentials/hashes/tickets?**
   - Follow `Credential Validation and Expansion`
4. **Need privilege escalation pathing?**
   - Follow `BloodHound and Graph Analysis`
5. **Need AD CS checks or abuse?**
   - Follow `AD CS Workflow`
6. **Need SMB loot / share intelligence / secret hunting?**
   - Follow `SMB Share Analysis`
7. **Need relay, coercion, or post-compromise actions?**
   - Follow `Relay and Lateral Movement`
8. **Need cracking / credential reuse loop?**
   - Follow `Cracking and Feedback Loop`

For exact tool substitutions and fallback order, read `references/tool-matrix.md`.

## Quick Start

### 1. Detect toolchain

Run:

```bash
python scripts/detect_ad_toolchain.py
```

Run it from the `skills/adscan/` directory or adapt the path to this plugin checkout.

Additional helpers in `scripts/`:

- `kerberoast.py` - SPN roast and ticket hash export wrapper
- `asreproast.py` - AS-REP roast wrapper for no-preauth users
- `bloodhound_collect.py` - bloodhound-python collection wrapper
- `ldap_enum.py` - LDAP object enumeration wrapper with NetExec fallback
- `vulnscan.py` - NetExec SMB CVE module wrapper for AD-focused fingerprinting
- `nmap_wrapper.py` - Nmap profile wrapper for AD discovery and SMB enumeration
- `relay_setup.py` - Responder and ntlmrelayx command builder for relay workflows

### 2. Create a workspace

Use a per-engagement directory and keep these subfolders:

- `recon/`
- `creds/`
- `bloodhound/`
- `adcs/`
- `shares/`
- `relay/`
- `cracking/`
- `notes/`

Naming and evidence rules are in `references/output-conventions.md`.

### 3. Record baseline context

Capture at minimum:

- target IPs / CIDRs / hostnames
- suspected domain / forest names
- known DCs / DNS servers
- foothold type: none / username / password / hash / ticket / cert / local admin
- allowed attack surface: internal only, SMB only, no poisoning, no spraying, etc.

## Main Workflows

### Unauth Enumeration

Use when you have no working AD credentials.

Preferred sequence:

1. Network and service discovery
   - `nmap` for host and service mapping
   - `massdns` if you need broad DNS coverage and it exists
2. Kerberos user discovery
   - `kerbrute userenum` or equivalent
3. SMB / LDAP anonymous checks
   - `NetExec smb` / `NetExec ldap` with safe unauth probes
4. Poisoning or capture only if appropriate
   - `Responder` only when the environment and scope allow it
5. Save candidate principals and interesting hosts

Do not jump into spraying before you know the domain naming pattern and blast radius.

### Credential Validation and Expansion

Use when you have usernames, passwords, NTLM hashes, tickets, or certificate-derived auth.

Preferred sequence:

1. Validate creds with `NetExec`
2. Check protocol reachability and host class
   - SMB, WinRM, MSSQL, LDAP
3. Enumerate what the identity can read or control
4. Test for local admin, share access, and session visibility
5. Loop newly discovered credentials back into validation

Good outputs here:

- valid credential list
- protocol-by-host success matrix
- hosts with admin access
- shares with read/write access

### BloodHound and Graph Analysis

Use when you have enough foothold to collect AD relationship data.

Preferred collectors:

- `bloodhound-python`
- `SharpHound`
- `rusthound-ce` if available and appropriate

Then:

1. Import into BloodHound CE or legacy BloodHound
2. Identify shortest or highest-value paths
3. Translate graph edges into executable actions
4. Verify each step with the underlying toolchain instead of assuming the graph is current

Do not treat BloodHound paths as proof of exploitability until you validate them.

### AD CS Workflow

Use when certificate services may exist or BloodHound/LDAP suggests CA/template exposure.

Preferred sequence:

1. `Certipy find`
2. Identify ESC-class issues and vulnerable templates
3. Request / relay / forge only when the path is justified
4. Convert certificate material into auth artifacts
5. Re-enter the credential validation workflow

Always record:

- CA name
- template name
- vulnerable principal/control relation
- resulting cert, ticket, or hash material

### SMB Share Analysis

Use when you have read access to shares or want low-noise secret hunting.

Preferred sequence:

1. Enumerate shares with `NetExec` or `SMBMap`
2. Deep file discovery with `Snaffler`, `MANSPIDER`, or controlled recursive listing
3. Pull files selectively with `smbclient`, `SMBMap`, or `rclone`
4. Search for credentials, keys, configs, scripts, exports, and office docs
5. Re-validate any discovered secrets immediately

High-value artifacts include:

- `web.config`, `.config`, `.ini`, `.ps1`, `.bat`
- backup archives
- password vault exports
- unattended install files
- database connection strings
- PFX / PEM / key material
- spreadsheet and document exports with credentials or PII

### Relay and Lateral Movement

Use when SMB signing is weak, coercion is possible, or you already have a privileged foothold.

Preferred components:

- `Responder`
- `impacket-ntlmrelayx`
- `Coercer`
- `Impacket` exec tools or `NetExec` exec paths
- `pypsrp` / WinRM for PowerShell remoting

Workflow:

1. Confirm this is in scope
2. Identify relayable targets
3. Stand up relay infrastructure
4. Trigger/auth coerce only where justified
5. Convert relay success into concrete host actions or dumped credentials

### Cracking and Feedback Loop

Use when you have hashes, AS-REP / Kerberoast material, or NTLM captures.

Preferred sequence:

1. Normalize captured material by type
2. Crack with `hashcat` or `john`
3. Feed results back into `NetExec` validation
4. Re-run BloodHound / AD CS / share checks with the stronger foothold

This loop is the closest thing to ADscan's automation engine: every new credential should reopen enumeration and path analysis.

## Tool Selection Rules

- Prefer `NetExec` as the main multi-protocol operator when available.
- Prefer `Impacket` for relay, dump, and precise protocol abuse.
- Prefer `Certipy` for any AD CS work.
- Prefer `BloodHound` for structural relationship analysis, but validate graph edges before acting.
- Prefer `SMBMap`/`Snaffler` for share triage over blind recursive looting.
- Prefer `hashcat` for large cracking workloads.
- Prefer low-noise workflows before noisy ones.

For substitutions, prerequisites, and per-tool fit, read `references/tool-matrix.md`.

## Output Discipline

For every meaningful step, report:

- objective
- tool used
- exact command run
- output location
- finding summary
- next decision

Keep this structured even in exploratory sessions. The point is to preserve the orchestration value that ADscan normally gives you.

## What This Skill Does Not Try To Reproduce

- ADscan's Docker/runtime management
- proprietary telemetry or licensing behavior
- product-specific UI/UX
- one-command auto-pwn chains
- ADscan-specific internal graph semantics beyond what open tools can provide

## References

- `references/tool-matrix.md` - primary tools, substitutes, and where each fits
- `references/workflows.md` - stage-by-stage operating playbooks
- `references/output-conventions.md` - evidence layout and reporting format

## Common Mistakes

- Starting with spraying before understanding domain naming and scope
- Treating BloodHound paths as executable truth without validation
- Using every installed tool instead of the smallest viable chain
- Dumping or looting broadly before identifying likely high-value hosts
- Forgetting to recycle newly found credentials back into enumeration
- Mixing outputs from multiple engagements into one directory
