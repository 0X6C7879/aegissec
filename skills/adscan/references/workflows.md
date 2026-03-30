# ADscan-Like Workflows

This file expands the high-level skill into concrete playbooks. Use the smallest workflow that matches the user's footing.

## 1. Initial Recon Workflow

Use when the user has targets but no valid AD credentials.

1. Confirm scope and constraints.
2. Identify likely DCs, DNS servers, and live hosts.
3. Run safe network discovery.
4. Enumerate likely domain users if Kerberos is reachable.
5. Probe SMB/LDAP anonymously only where appropriate.
6. Decide whether to stay low-noise or escalate to spraying/capture.

Expected outputs:

- live host list
- probable DC list
- domain naming clues
- initial username candidates
- SMB signing / LDAP anonymous / WinRM exposure hints

## 2. Credential Validation Workflow

Use when the user has one or more of:

- usernames
- passwords
- NTLM hashes
- Kerberos tickets
- certificates / PFX material

1. Normalize each credential type.
2. Validate against the smallest meaningful host set first.
3. Expand to subnet or host list once one credential proves useful.
4. Record protocol-specific success.
5. Identify admin context, share access, and sessions.

Expected outputs:

- validated credentials
- host/protocol auth matrix
- admin-capable hosts
- share-access map

## 3. BloodHound Workflow

Use when authenticated collection is feasible.

1. Choose collector based on foothold and platform.
2. Collect with the narrowest scope that still answers the question.
3. Import into BloodHound.
4. Identify shortest paths, high-value sessions, ACL control, delegation, and AD CS edges.
5. Convert only the best candidate edges into actions.
6. Validate each action with the underlying toolchain.

Expected outputs:

- collection artifact path
- imported graph context
- prioritized attack paths
- concrete next-step actions

## 4. AD CS Workflow

Use when:

- BloodHound shows PKI-related edges
- LDAP/host recon suggests enterprise CA deployment
- user asks specifically about AD CS, ESC, cert abuse, or pass-the-certificate

1. Enumerate with `Certipy find`.
2. Classify vulnerable templates/permissions.
3. Decide whether the path gives privilege escalation, persistence, or lateral movement.
4. Request or forge only the material needed.
5. Turn resulting cert artifacts into a usable auth context.
6. Feed the new auth back into validation and graph analysis.

Expected outputs:

- vulnerable CAs/templates
- relevant ESC path
- resulting cert/PFX/ticket/hash artifacts

## 5. SMB Share Intelligence Workflow

Use when authenticated SMB access exists.

1. Enumerate shares and permissions.
2. Prioritize by business relevance and writeability/readability.
3. Run secret-hunting or triage tooling.
4. Pull only the most promising files first.
5. Extract credentials, keys, configs, connection strings, exports, and scripts.
6. Validate discovered secrets immediately.

Expected outputs:

- share inventory
- prioritized artifact list
- extracted credentials/configs
- credential provenance notes

## 6. Relay Workflow

Use when the environment and rules of engagement allow poisoning/relay.

1. Confirm SMB signing and relayability.
2. Prepare target list carefully.
3. Start relay listener.
4. Generate or wait for authentication.
5. Convert relay success into one concrete outcome:
   - dump
   - command exec
   - SOCKS access
   - lateral session
6. Save proof and decide whether to continue or stop.

Expected outputs:

- relayable target list
- relay logs
- concrete outcome from successful relay

## 7. Crack and Recycle Workflow

Use whenever captured material is crackable offline.

1. Normalize hash/ticket format.
2. Choose cracking mode/tool.
3. Track the source of every recovered secret.
4. Re-validate cracked credentials with `NetExec` or the relevant protocol tool.
5. Re-run graph/AD CS/share checks with the stronger identity.

Expected outputs:

- cracked credential list
- source-to-secret mapping
- revalidation results

## Stopping Rules

Stop and summarize when:

- the requested objective is met
- the next step would exceed scope/noise constraints
- the user needs to make a strategic choice
- tooling gaps prevent reliable execution

When stopping, always return:

- what was done
- what worked
- what evidence was saved
- best next move
