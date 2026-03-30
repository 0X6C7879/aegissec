# Output Conventions

Preserve the orchestration value of ADscan by keeping output structured and reusable.

## Workspace Layout

Create one root directory per engagement, for example:

```text
engagement-name/
  recon/
  creds/
  bloodhound/
  adcs/
  shares/
  relay/
  cracking/
  notes/
```

## File Naming

Use predictable names:

- `recon/nmap_tcp.txt`
- `recon/kerbrute_userenum.txt`
- `creds/validated_netexec_smb.txt`
- `bloodhound/collector_YYYYMMDD_HHMM.zip`
- `adcs/certipy_find.txt`
- `shares/share_inventory.txt`
- `relay/ntlmrelayx.txt`
- `cracking/hashcat_show.txt`
- `notes/findings.md`

## Per-Step Logging Template

For each material action, capture:

```text
Objective:
Tool:
Command:
Inputs:
Output file(s):
Key findings:
Next action:
```

## Credential Tracking

For every credential or artifact found, record:

- source host/share/path/tool
- credential type: cleartext / NTLM / TGT / PFX / token / API secret
- principal it belongs to
- validation status
- where it has already worked

## Graph Findings Tracking

When a BloodHound path or ACL edge matters, record:

- source principal
- target principal/object
- edge/control type
- validation status: untested / confirmed / failed
- exact command used to validate

## Noise and Safety Notes

Tag steps as one of:

- `low-noise`
- `moderate-noise`
- `high-noise`

Call this out before proposing:

- spraying
- Responder/poisoning
- relay/coercion
- broad recursive looting
- any write action in AD

## End-of-Run Summary Format

When pausing or finishing, summarize in this order:

1. Objective achieved / not achieved
2. Best findings
3. New credentials or privileges
4. Evidence locations
5. Recommended next step
