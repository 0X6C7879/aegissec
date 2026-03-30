# adpwn

`adpwn` 是一个 OpenCode 插件，封装了实用的 Active Directory 渗透测试技能，专为授权安全测试设计。该插件包含以下技能：`adpwn` 提供 `adscan`、`tunnel`、`movement`、`privesc`、`persistence`、`bypass` 和 `c2` 等技能。

## 技能总览

| 技能 | 用途 |
|------|------|
| **adscan** | AD 侦察、凭证验证、BloodHound 收集、AD CS 滥用、SMB 共享分析、Relay 准备和凭证反馈循环 |
| **tunnel** | Ligolo-ng、Chisel、frp、SSH、socat、proxychains 和内网穿透设置 |
| **movement** | Impacket、NetExec、Evil-WinRM、WMI、DCOM、PtH、PtT 和 RDP 横向移动 |
| **privesc** | Windows 本地提权和立足点后的权限扩展 |
| **persistence** | 获得特权访问后的域和主机持久化技术 |
| **bypass** | AV/EDR 规避指导，包括 Defender 和其他商业产品的对抗方案 |
| **c2** | Metasploit、Sliver、Havoc、Mythic 和攻击基础设施的权衡分析 |

## 设计理念

插件层设计得非常轻薄。`adpwn` 向 OpenCode 注入启动引导指南，使内置技能被有意识地选中，而不是临时拼凑通用的 AD 工作流程。核心的技术实践位于各个技能文件夹及其参考文献中。

本项目不调用 `adscan` 二进制文件，而是使用底层工具和精心设计的技能指南来重建工作流程。

## 仓库结构

```
adpwn/
├─ package.json              # 插件包配置
├─ index.js                  # 插件入口点
├─ .opencode/plugins/adpwn.js # OpenCode 引导插件
├─ .opencode/skills/         # 技能目录 (标准位置)
│  ├─ adscan/               # AD 侦察与收集
│  ├─ tunnel/               # 网络穿透与隧道
│  ├─ movement/             # 横向移动
│  ├─ privesc/             # 本地提权
│  ├─ persistence/         # 持久化
│  ├─ bypass/              # 免杀与规避
│  ├─ c2/                  # 命令控制框架
│  └─ shared/              # 共享工具和参考文献
└─ tests/                   # 测试套件
```

## 技能详细说明

### adscan - AD 侦察编排

当需要执行 Active Directory 渗透测试编排时使用，尤其是域枚举、凭证验证、BloodHound 收集、AD CS 滥用、SMB 共享分析、Relay/破解工作流，或使用 NetExec、Impacket、Certipy、BloodHound、kerbrute、Responder、hashcat、SMBMap、Snaffler 等底层工具替代 ADscan 时。

**内置脚本助手：**
- `kerberoast.py` - SPN 漫游和票据哈希导出封装
- `asreproast.py` - 无预认证用户的 AS-REP 漫游封装
- `bloodhound_collect.py` - bloodhound-python 收集封装
- `ldap_enum.py` - LDAP 对象枚举封装，支持 NetExec 回退
- `vulnscan.py` - NetExec SMB CVE 模块封装，用于 AD 指纹识别
- `nmap_wrapper.py` - Nmap 配置封装，用于 AD 发现和 SMB 枚举
- `relay_setup.py` - Responder 和 ntlmrelayx 命令构建器，用于 Relay 工作流

### tunnel - 网络穿透

当下一步被网络可达性阻塞而非凭证缺失时使用。适用于 Ligolo-ng、Chisel、frp、proxychains、SSH 转发、SOCKS 代理、反向隧道等场景。

**内置脚本助手：**
- `ligolo_setup.py` - proxy/agent/route/start 命令规划器
- `chisel_pivot.py` - 服务器/客户端 SOCKS 穿透命令规划器
- `proxychains_gen.py` - 为 SOCKS 端点生成 proxychains 配置

### movement - 横向移动

在获得凭证后，当主要目标是扩展对 Windows 或 AD 主机的访问时使用。适用于 Impacket、NetExec、Evil-WinRM、WMI、DCOM、SMB exec、PtH、PtT、RDP 等技术。

**内置脚本助手：**
- `pth_spray.py` - 通过 NetExec 进行 PtH 喷洒
- `wmiexec_run.py` - WMI 远程命令封装
- `evil_winrm_wrap.py` - Evil-WinRM 连接辅助工具，包含大文件上传前台聚焦与大小校验提示
- `smb_exec.py` - psexec/smbexec/atexec 执行封装
- `dcom_exec.py` - DCOM 执行封装，支持 ShellWindows/MMC20 路径
- `rdp_connect.py` - xfreerdp 命令构建器，支持 restricted-admin 模式

**WinRM 上传最佳实践：**
- 上传大文件时保持终端前台，不要切走或插入其他命令
- 等待 `upload` / `put` 明确结束后再执行后续动作
- 上传后至少比较远端和本地文件大小，一致后再视为上传成功
- `evil_winrm_wrap.py` 会输出建议的远端大小检查命令

### privesc - 本地提权

在建立立足点后，用于 Windows 主机本地提权。适用于 WinPEAS、Seatbelt、Potato 系列技术、令牌滥用、服务滥用等场景。

**内置脚本助手：**
- `winpeas_runner.py` - WinPEAS 执行和结果提取
- `potato_launcher.py` - Potato 系列命令和要求构建器
- `service_misconfig.py` - 服务 ACL/未引用路径检查命令生成器
- `cred_harvest.py` - lsassy/nanodump/SAM 凭证收集封装
- `always_install.py` - AlwaysInstallElevated MSI 和 msiexec 命令构建器

### persistence - 持久化

在获得有意义权限后，需要持久化访问（可承受凭证变更或主机重启）时使用。适用于 Golden Ticket、Silver Ticket、DCSync 派生材料、AD CS 证书持久化、计划任务、WMI 事件订阅等技术。

**内置脚本助手：**
- `golden_ticket.py` - Golden Ticket 生成封装
- `silver_ticket.py` - Silver Ticket 生成封装
- `dcsync.py` - secretsdump DCSync 封装
- `cert_persist.py` - Certipy find/request/auth 封装
- `machine_account.py` - addcomputer 封装，用于机器账户持久化路径
- `host_persist.py` - 注册表 Run 键和计划任务命令构建器

### bypass - 免杀与规避

当障碍是端点保护而非访问缺失或路由问题时使用。适用于 AV 或 EDR 阻止执行，需要 AMSI 绕过、ETW 修补、用户态反挂钩、LOLBins、加载器权衡或 BYOVD 决策支持。

**内置脚本助手：**
- `amsi_snippets.py` - AMSI 绕过代码片段库
- `etw_patch.py` - ETW 修补代码片段库
- `lolbin_exec.py` - LOLBin 执行命令构建器，带检测说明

### c2 - 命令控制

当需要攻击者管理的命令控制层而非一次性 shell 时使用。适用于 Metasploit、msfconsole、msfvenom、Sliver、Havoc、Mythic、payload 分阶段、监听器设计和攻击基础设施权衡。

**内置脚本助手：**
- `msfvenom_gen.py` - msfvenom 命令和处理器块生成器
- `sliver_setup.py` - Sliver 服务器/监听器/implant 命令辅助工具
- `msf_handler.py` - Metasploit multi/handler .rc 生成器
- `redirector_setup.py` - socat 和 iptables 重定向器命令构建器
- `modern_c2_setup.py` - Havoc 和 Mythic 监听器/配置生成器

## 安装

### 方法一：全局安装（推荐）

推荐直接使用仓库内置工具完成安装、体检和卸载：

```bash
# 安装到 OpenCode 全局目录，并自动创建 adscan / movement / tunnel 等短名称别名
python tools/install_global.py

# 查看当前安装状态、bundle 路径和别名状态
python tools/self_check.py

# 卸载 adpwn 全局安装和短名称别名
python tools/uninstall_global.py
```

如果需要先预览路径而不落盘：

```bash
python tools/install_global.py --dry-run
python tools/uninstall_global.py --dry-run
```

下面是这些脚本实际执行的目录约定：

参考 `wooyun-legacy`、`evasion-subagents`、`superpowers` 等插件的常见约定，推荐把 **插件代码** 放到 `local-plugins/`，把 **技能包** 放到全局 `skills/` 目录：

```bash
# 1. 安装插件代码（bootstrap）
mkdir -p ~/.config/opencode/local-plugins/adpwn
cp index.js ~/.config/opencode/local-plugins/adpwn/
cp package.json ~/.config/opencode/local-plugins/adpwn/
mkdir -p ~/.config/opencode/local-plugins/adpwn/.opencode/plugins
cp .opencode/plugins/adpwn.js ~/.config/opencode/local-plugins/adpwn/.opencode/plugins/

# 2. 注册插件入口
mkdir -p ~/.config/opencode/plugins
printf "export { AdpwnPlugin } from '../local-plugins/adpwn/index.js';\n" > ~/.config/opencode/plugins/adpwn.js

# 3. 安装全局技能包
mkdir -p ~/.config/opencode/skills/adpwn
cp SKILL.md ~/.config/opencode/skills/adpwn/
mkdir -p ~/.config/opencode/skills/adpwn/skills
cp -r .opencode/skills/* ~/.config/opencode/skills/adpwn/skills/

# 4. 在 ~/.config/opencode/opencode.json 的 plugin 数组中添加 "adpwn"

# 5. 可选：为高频子技能创建短名称别名
cd ~/.config/opencode/skills
ln -sf adpwn/skills/adscan adscan
ln -sf adpwn/skills/tunnel tunnel
ln -sf adpwn/skills/movement movement
ln -sf adpwn/skills/privesc privesc
ln -sf adpwn/skills/persistence persistence
ln -sf adpwn/skills/bypass bypass
ln -sf adpwn/skills/c2 c2
```

这样安装后：
- 插件 bootstrap 走 `~/.config/opencode/local-plugins/adpwn`
- 技能内容走 `~/.config/opencode/skills/adpwn`
- `install_global.py` 会自动刷新 `adscan`、`movement`、`tunnel`、`privesc`、`persistence`、`bypass`、`c2` 这些短名称别名
- 结构与其他全局插件更一致，升级时也更容易覆盖

安装完成后，你可以使用：
- `skill(adpwn)` - 查看整套 AD 工作流的总入口
- `skill(adscan)` - 加载 AD 侦察技能
- `skill(tunnel)` - 加载网络穿透技能
- `skill(movement)` - 加载横向移动技能
- `skill(privesc)` - 加载本地提权技能
- `skill(persistence)` - 加载持久化技能
- `skill(bypass)` - 加载免杀技能
- `skill(c2)` - 加载 C2 技能

### 方法二：本地开发安装

如果你只是在当前仓库里开发和调试，也可以直接用仓库目录作为本地插件包：

```json
{
  "plugin": ["adpwn"]
}
```

常见的本地开发布局是将仓库放置在 OpenCode 可以将其作为插件包加载的位置，类似于 `~/.config/opencode/local-plugins/` 下的其他本地插件。

## 验证

安装后，验证插件和技能已加载：

```bash
# 检查插件是否加载
opencode debug config | grep adpwn

# 检查技能包和子技能是否可用
opencode debug skill | grep -E "adpwn|adscan|tunnel|movement|privesc|persistence|bypass|c2"

# 运行 adpwn 自检
python tools/self_check.py

# 验证全局技能包结构
ls -la ~/.config/opencode/skills/adpwn
ls -la ~/.config/opencode/skills/adpwn/skills/adscan
```

运行测试套件以验证仓库完整性：

```bash
# 在插件目录下运行
cd ~/.config/opencode/local-plugins/adpwn
python -m pytest tests -q
```

### 预期插件行为

加载后，插件会注入启动引导指南，告诉 OpenCode 使用内置技能：
- 从 `adscan` 开始进行发现和凭证扩展
- 当可达性是障碍时使用 `tunnel`
- 使用 `movement` 进行主机到主机的执行
- 使用 `privesc` 进行本地 Windows 提权
- 特权访问后使用 `persistence`
- AV/EDR 阻止执行时使用 `bypass`
- 使用 `c2` 进行框架和攻击基础设施决策

## 工具链检测脚本

在渗透测试前或更改攻击基础设施后，使用内置的检测辅助工具：

```bash
python .opencode/skills/shared/scripts/detect_adpwn_toolchain.py
python .opencode/skills/adscan/scripts/detect_ad_toolchain.py
```

这些脚本输出 JSON，可快速查看哪些工具可用以及需要哪些回退方案。

## 最小可行工具集

| 技能 | 必需工具 |
|------|----------|
| **adscan** | `nxc`, `impacket-*`, `bloodhound-python`, `certipy`, `kerbrute` |
| **tunnel** | `ligolo-ng`, `chisel`, `ssh`, `socat`, `proxychains` |
| **movement** | `nxc`, `wmiexec.py`, `psexec.py`, `evil-winrm`, `xfreerdp` |
| **privesc** | `winpeas`, `seatbelt`, PowerShell, Windows 内置服务工具 |
| **persistence** | `certipy`, `impacket-*`, 计划任务工具, WMI 工具 |
| **bypass** | AMSI/ETW 能力工具, 受信任的 LOLBins, 加载器或分阶段工作流 |
| **c2** | `msfconsole`, `msfvenom`, `sliver`, `havoc` 或同类攻击框架 |

## 使用示例

- **adscan**: "Start with `adscan`. I have `corp.local`, one user credential, and need recon plus credential validation."  
  （从 `adscan` 开始。我有 `corp.local` 域和一个用户凭证，需要侦察和凭证验证。）

- **tunnel**: "Use `tunnel`. I have a foothold on a dual-homed host and need access to `10.20.30.0/24`."  
  （使用 `tunnel`。我在双宿主主机上有立足点，需要访问 `10.20.30.0/24`。）

- **movement**: "Use `movement`. I validated local admin over WinRM on two servers and need the quietest host-to-host expansion path."  
  （使用 `movement`。我通过 WinRM 在两台服务器上验证了本地管理员权限，需要最隐蔽的主机扩展路径。）

- **privesc**: "Use `privesc`. I have a low-priv shell on a workstation and need admin or SYSTEM with minimal noise."  
  （使用 `privesc`。我在工作站上有低权限 shell，需要以最小痕迹获取 admin 或 SYSTEM 权限。）

- **persistence**: "Use `persistence`. I now have domain admin and need a durable but justified re-entry path with cleanup notes."  
  （使用 `persistence`。我现在有域管理员权限，需要一个持久但合理的重新入场路径，并附带清理记录。）

- **bypass**: "Use `bypass`. Defender is killing my tooling before execution and I need the least invasive workaround."  
  （使用 `bypass`。Defender 在执行前杀掉我的工具，我需要最小侵入性的变通方案。）

- **c2**: "Use `c2`. I need a framework choice and listener plan for a staged multi-host operation."  
  （使用 `c2`。我需要一个框架选择和监听器计划，用于分阶段的多主机操作。）

## 故障排除

- 插件未出现在 `opencode debug config` 中：确认包已本地安装且 `adpwn` 存在于 `plugin` 数组中。
- `opencode debug skill` 未显示内置技能：重新运行 `python tests/plugin-bootstrap-check.py` 和 `python tools/validate_skill_refs.py` 以确认引导线和引用路径正确。
- 检测脚本输出看起来不完整：直接运行 `python .opencode/skills/shared/scripts/detect_adpwn_toolchain.py` 或 `python .opencode/skills/adscan/scripts/detect_ad_toolchain.py` 并验证所需二进制文件在 `PATH` 上。
- 编辑技能文档后测试失败：验证每个非 `adscan` 技能都包含 `Quick Start`、`Selection Rules`、`Output Discipline`、`When To Switch` 和 `References` 标题。

## 安全边界与高风险操作

本仓库仅用于授权安全测试、实验环境、研究和在明确允许评估的环境中进行攻击者辅助编排。**禁止将其用于未经授权的访问或非法活动。**

高噪声或高风险操作需要格外小心、更强有力的理由和清晰的证据收集：

- Relay 攻击、Responder 风格 poisoning 和广泛的身份验证强制
- 域范围收集、DCSync、票据伪造和证书滥用
- 内核漏洞、BYOVD 风格规避或广泛的 EDR 篡改
- 持久化可承受重启或凭证轮换
- payload 分阶段或 C2 部署扩大爆炸半径超过单台主机

## 开发说明

- 插件入口点：`index.js`
- OpenCode 引导插件：`.opencode/plugins/adpwn.js`
- 精确技能引导验证器：`tests/plugin-bootstrap-check.py`
- 共享辅助脚本和引用位于 `.opencode/skills/shared/` 下

## 法律声明

本仓库仅供授权安全测试、教育研究和在您明确允许评估的环境中使用。未经授权访问计算机系统是违法行为。使用本工具即表示您同意仅在法律允许的范围内使用，并自行承担风险。
