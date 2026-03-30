# AD Tool Matrix

Use this file after running tool detection. It tells you what each tool is good for, when to prefer it, and what to use if it is missing.

## Primary Orchestrators

| Tool | Best for | Prefer when | Fallbacks |
|---|---|---|---|
| `NetExec` | SMB/LDAP/WinRM/MSSQL enumeration, credential validation, spraying, share checks, local admin validation | You need one operator-friendly interface across multiple AD protocols | `crackmapexec` if still present, direct `Impacket`, native client tools |
| `Impacket` | relay, dump, remote execution, ticket/cert-auth adjacent operations | You need precision or a capability NetExec abstracts poorly | direct protocol tools, native Windows tools |
| `BloodHound` / `BloodHound CE` | graph analysis of AD privilege relationships | You have enough data to reason structurally | none; if missing, fall back to manual ACL/session reasoning |
| `Certipy` | AD CS enumeration and abuse | Any CA/template exposure exists or BloodHound/LDAP hints at AD CS | limited manual LDAP + certificate handling |

## Discovery and Enumeration

| Tool | Role | Notes |
|---|---|---|
| `nmap` | host, port, service discovery | Baseline network visibility before protocol workflows |
| `massdns` | fast DNS brute/validation | Useful when DNS enumeration is broad and performance matters |
| `kerbrute` | user enumeration and Kerberos spraying | High-signal for AD usernames and initial validation |
| `SMBMap` | SMB share triage and file interaction | Great for listing access and pulling selective files |
| `Snaffler` | high-value share content discovery | Prefer for noisy share estates where you need prioritization |
| `rclone` | controlled large-scale file sync from shares | Use when share access is broad and you need stable copying |

## Credential Access and Expansion

| Tool | Role | Notes |
|---|---|---|
| `Responder` | poison/capture NTLM | Only when in scope and noise is acceptable |
| `impacket-ntlmrelayx` | NTLM relay | Pair with `Responder` or `Coercer` when signing/targets allow |
| `Coercer` | force remote auth | Use to create relay opportunities |
| `hashcat` | offline cracking at scale | Preferred for NTLMv2, Kerberoast, AS-REP workloads |
| `john` | alternative cracking workflows | Useful when format support or local preference favors it |
| `pypykatz` | parse LSASS/minidump style secrets | Use after dump acquisition, not for initial discovery |

## BloodHound Collectors

| Collector | Prefer when | Notes |
|---|---|---|
| `bloodhound-python` | Linux-first workflow with valid creds | Easy fit for operator laptops and containers |
| `SharpHound` | You have Windows execution on a host | Often richest in-domain collector |
| `rusthound-ce` | You want Linux-native collection with strong performance | Mirrors modern CE workflows well |

## Decision Rules

1. If `NetExec` exists, use it as the default entry point for protocol validation and host triage.
2. If `NetExec` does not exist but `crackmapexec` does, use it cautiously and call out syntax differences.
3. If neither exists, split the workflow by protocol and use `Impacket`, `ldapsearch`, `rpcclient`, `smbclient`, `evil-winrm`, or `pypsrp` as available.
4. If `BloodHound` tooling is missing, do not fake graph analysis. Fall back to explicit ACL, group, session, and delegation reasoning.
5. If `Certipy` is missing, keep AD CS in reconnaissance mode unless you have another proven certificate workflow.
6. If `Responder` or relay tooling is out of scope, skip poisoning entirely instead of suggesting it implicitly.

## Recommended v1 Capability Baseline

The skill is considered strong even if only this subset is present:

- `nmap`
- `NetExec`
- `Impacket`
- `BloodHound` collector + UI/import path
- `Certipy`
- one cracking tool: `hashcat` or `john`
- one share triage tool: `SMBMap` or `Snaffler`

## Mapping to ADscan-Like Outcomes

| Desired outcome | Minimal open-tool chain |
|---|---|
| Domain/user discovery | `nmap` + `kerbrute` + `NetExec ldap/smb` |
| Credential validation | `NetExec` |
| Local admin / loot | `NetExec` + `Impacket` |
| Attack path reasoning | collector + `BloodHound` |
| AD CS exploitation | `Certipy` |
| Share secret hunting | `NetExec --shares` + `SMBMap`/`Snaffler` |
| Relay path | `Responder` + `ntlmrelayx` + optional `Coercer` |
| Crack-and-reuse loop | captured hashes + `hashcat`/`john` + `NetExec` |
