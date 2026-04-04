---
name: reverse-engineering
description: 逆向工程技能 - 二进制分析、协议逆向、Hook/注入、脱壳、防护对抗、漏洞挖掘。当你涉及逆向分析、反编译、二进制分析、Hook、Frida、LLDB调试、协议分析、加密算法还原、防护绕过、脱壳、签名伪造时必须使用此技能。即使用户只是说"看看这个app"或"分析一下这个二进制"，也应触发。
---

# 逆向工程技能 (Reverse Engineering Skill)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[逆向核心清单]** ① 攻击面枚举是第一步：输入点→信任边界→数据流→敏感资产 ② 由外向内四层递进：网络→应用→运行时→二进制，每层穷尽再深入 ③ 遇到防护先观察行为特征，对比已知模式，逐步验证假设
> **[OPSEC三禁]** ❌明文存储/传输提取的密钥凭证 ❌在非授权环境执行exploit ❌不清理临时文件和调试痕迹
> **[分析铁律]** 每个结论必须有证据链（工具输出/内存dump/流量截图），"看起来像"不算分析结果

逆向分析时，强制遵守：
1. **攻击面先行**：任何目标的第一步是枚举所有输入点和信任边界，不急于深入某个点
2. **四层模型**：网络层→应用层→运行时层→二进制层，由外向内逐层深入
3. **证据驱动**：每个发现必须有工具输出佐证，禁止主观猜测（无证据的结论无法复现验证，在安全研究中毫无价值）
4. **OPSEC纪律**：敏感数据禁明文存储，临时文件即用即删，不在非授权环境操作
5. **防护对抗**：先识别防护类型→定位检测函数→最小化修改使其返回"正常"值
6. **工具链优先**：能用现成工具(otool/strings/lldb/class-dump)就不手动分析
7. **记录复现**：关键步骤记录命令和输出，确保可复现

---

## 完整逆向分析流程（手动 /reverse-engineering 或专项分析时执行）

### Phase 1: 攻击面枚举（任何目标第一步）

1. **输入点识别**：
   - 网络接口（HTTP/HTTPS/WebSocket/gRPC/私有协议）
   - 文件解析（配置文件/导入文件/拖拽文件/剪贴板）
   - IPC通信（XPC/Mach ports/Unix Socket/Named Pipe）
   - URL Scheme / Universal Links / Deep Links
   - 传感器/外设输入（蓝牙/USB/NFC）
   - 用户界面输入（文本框/设置项/命令行参数）

2. **信任边界标识**：
   - 客户端 ↔ 服务端
   - 进程 ↔ 内核
   - 用户态 ↔ 特权态
   - 内网 ↔ 外网
   - 沙盒内 ↔ 沙盒外

3. **敏感数据流追踪**：
   - 凭证（密码/Token/Session）：产生→传输→存储→使用→销毁
   - 密钥（加密密钥/API Key）：生成→存储→使用→轮转
   - 个人信息（姓名/手机/地址）：采集→传输→存储→展示→删除
   - 计费数据（余额/VIP状态/功能开关）：服务端下发→本地缓存→校验→展示

### Phase 2: 网络层分析（抓包与协议逆向）

4. **抓包工具链**：

| 工具 | 适用场景 | 关键用法 |
|------|----------|----------|
| **Charles Proxy** | HTTP/HTTPS抓包，macOS/iOS首选 | 安装根证书→启用SSL Proxying→添加目标域名→查看请求/响应 |
| **mitmproxy** | 命令行抓包+脚本化修改 | `mitmproxy --mode regular`→配置代理→脚本自动修改请求/响应 |
| **Wireshark** | TCP/UDP/私有协议深度分析 | 捕获→过滤(ip.addr==TARGET)→Follow TCP Stream→分析包结构 |
| **tcpdump** | 服务器端轻量抓包 | `tcpdump -i any -w cap.pcap host TARGET -c 1000` |
| **Proxyman** | macOS原生HTTP调试代理 | 自动配置系统代理+证书，支持Map Local/Remote |
| **Burp Suite** | 渗透测试专用 | Repeater重放/Intruder爆破/Scanner扫描 |

5. **抓包实战流程**：
   - ① 配置代理(HTTP/SOCKS)→安装CA证书→确认能抓到HTTPS明文
   - ② 触发目标功能→记录所有请求(URL/参数/Header/Body)
   - ③ 分析认证流程：Token格式(JWT/自定义)→刷新机制→签名算法
   - ④ 识别加密参数：哪些字段是Base64/Hex/自定义编码
   - ⑤ 尝试篡改：修改参数值→观察响应变化→确认服务端校验逻辑
   - ⑥ 处理证书固定：报错"SSL handshake failed"→Hook SSL验证函数/替换证书

6. **协议逆向详解**：
   - **JSON API**：直接可读，重点分析认证头/签名参数/加密字段
   - **Protobuf**：`protoc --decode_raw < data.bin`解码→推断字段含义→还原.proto文件
   - **MessagePack**：用msgpack库解码→分析结构
   - **自定义二进制**：“魔数+版本+长度+类型+负载”模式，用xxd/hex编辑器分析字节序
   - **WebSocket**：Charles可直接查看帧内容，分析消息类型/序列号/心跳机制
   - **gRPC**：拦截HTTP/2流量→提取Protobuf负载→还原服务定义

7. **Windsurf环境命令**：

| 命令 | 用途 |
|------|------|
| `timeout 30 curl -v -k URL` | 查看HTTP响应头/TLS信息 |
| `timeout 30 curl -x http://127.0.0.1:8080 -k URL` | 通过代理抓包 |
| `tcpdump -i any -c 100 -w capture.pcap host TARGET` | 抓包（需sudo） |
| `openssl s_client -connect HOST:PORT` | TLS证书和握手信息 |
| `openssl x509 -in cert.pem -text -noout` | 解析证书详情 |
| `nmap -sV -p- TARGET` | 端口扫描+服务识别 |
| `protoc --decode_raw < data.bin` | Protobuf原始解码 |
| `python3 -c "import msgpack; print(msgpack.unpackb(open('data.bin','rb').read()))"` | MessagePack解码 |

### Phase 3: 应用层分析

7. **静态分析**：
   - 获取可分析形态（反编译/解包/脱壳）
   - 提取字符串/密钥/配置/URL（strings + grep）
   - 定位关键逻辑（认证/加密/计费/授权/License检查）
   - 分析依赖库和框架版本

8. **macOS逆向专项**（重点）：

**8a. 基础信息收集**：

| 命令 | 用途 |
|------|------|
| `file BINARY && lipo -info BINARY` | 架构/格式识别(x86_64/arm64/Universal) |
| `otool -L BINARY` | 查看动态库依赖(识别框架:SwiftUI/AppKit/Electron) |
| `otool -l BINARY \| grep -A5 LC_RPATH` | 查看RPATH(动态库搜索路径) |
| `otool -ov BINARY` | 导出ObjC类/方法/协议列表 |
| `class-dump BINARY > dump.h` | ObjC头文件导出(含属性/方法签名) |
| `nm -g BINARY \| grep -i '_OBJC_CLASS'` | 导出符号表/类名 |
| `swift-demangle < mangled_names.txt` | Swift符号还原(类名+方法名) |
| `strings BINARY \| grep -iE 'key\|password\|token\|secret\|api\|license'` | 提取敏感字符串 |

**8b. 应用信息与配置**：

| 命令 | 用途 |
|------|------|
| `plutil -p APP.app/Contents/Info.plist` | 解析Info.plist(版本/Bundle ID/URL Scheme/权限) |
| `codesign -dv --entitlements :- APP.app` | 查看代码签名+Entitlements权限 |
| `codesign -vvv APP.app` | 验证签名有效性 |
| `mdls APP.app` | macOS元数据(版本/签名/沙盒) |
| `defaults read BUNDLE_ID` | 读取应用UserDefaults存储 |
| `sqlite3 ~/Library/Containers/BUNDLE_ID/Data/Library/...` | 读取应用SQLite数据库 |
| `find ~/Library -name 'BUNDLE_ID*' -o -name 'APP_NAME*' 2>/dev/null` | 查找应用所有本地存储 |
| `ls -la ~/Library/Application\ Support/APP_NAME/` | 查看应用数据目录 |

**8c. macOS安全机制**：

| 机制 | 说明 | 逆向影响 |
|------|------|----------|
| **SIP** (System Integrity Protection) | 保护系统目录/进程 | 无法注入系统进程，需`csrutil disable` |
| **AMFI** (Apple Mobile File Integrity) | 代码签名执行策略 | 未签名/篡改的dylib无法加载 |
| **Gatekeeper** | 应用公证检查 | 可`xattr -d com.apple.quarantine`绕过 |
| **App Sandbox** | 文件/网络/IPC访问限制 | 沙盒应用数据在`~/Library/Containers/` |
| **Hardened Runtime** | 禁止代码注入/调试 | 需`--disable-library-validation`entitlement或关SIP |
| **Keychain** | 密钥/密码/证书安全存储 | `security find-generic-password -s SERVICE_NAME -w` |
| **TCC** (Transparency Consent Control) | 隐私权限控制 | DB在`/Library/Application Support/com.apple.TCC/TCC.db` |

**8d. macOS dylib注入**：
- **DYLD_INSERT_LIBRARIES**：`DYLD_INSERT_LIBRARIES=hook.dylib ./app`（需关SIP或无Hardened Runtime）
- **insert_dylib**：修改Mach-O添加LC_LOAD_DYLIB命令
- **Frida**：`frida -n "AppName" -l hook.js`无需修改二进制
- **LLDB注入**：`lldb -p PID`→`expr (void*)dlopen("/path/hook.dylib", 2)`

**8e. Keychain提取**：
```bash
# 列出所有Keychain条目
security dump-keychain -d login.keychain-db
# 查找特定服务的密码
security find-generic-password -s "SERVICE_NAME" -w
# 查找特定账户的密码
security find-internet-password -s "server.com" -a "username" -w
```

9. **iOS应用专项**：
   - 脱壳：`frida-ios-dump`/`CrackerXI+`/`bfdecrypt`→获取解密IPA
   - 文件系统：`/var/mobile/Containers/Data/Application/`下查找应用数据
   - Keychain：`keychain-dumper`/`objection`提取Keychain条目
   - NSUserDefaults：`find /var/mobile -name '*.plist' | xargs plutil -p`
   - 调试：`iproxy 1234 1234`→`lldb`→`process connect connect://localhost:1234`

10. **Android应用专项**：

| 命令 | 用途 |
|------|------|
| `apktool d APP.apk` | APK解包+资源反编译 |
| `jadx -d output/ APP.apk` | DEX→Java反编译 |
| `unzip APP.apk -d extracted/` | 直接解压查看资源 |
| `aapt dump badging APP.apk` | 查看包信息/权限/组件 |
| `keytool -printcert -jarfile APP.apk` | 查看签名证书信息 |
| `adb shell run-as PACKAGE cat databases/app.db > /tmp/app.db` | 提取应用数据库 |
| `frida -U -n APP -l hook.js` | Frida Hook(需USB连接) |

### Phase 4: 运行时分析

11. **LLDB调试专项**：

| 命令 | 用途 |
|------|------|
| `lldb -o 'target create BINARY'` | 静态加载分析 |
| `lldb -p PID` | 附加到运行中进程 |
| `b -[ClassName methodName]` | ObjC方法断点 |
| `b swift_function_name` | Swift函数断点 |
| `register read` | 查看寄存器(x0-x7=参数, x0=返回值) |
| `register write x0 1` | 修改返回值(绕过检查) |
| `po $x0` | 打印ObjC对象 |
| `memory read --size 1 --count 100 ADDR` | 读内存 |
| `memory find ADDR ADDR+0x10000 -s "keyword"` | 内存搜索字符串 |
| `image dump symtab MODULE` | 导出模块符号表 |
| `image lookup -rn "license\|verify\|check" MODULE` | 正则搜索符号 |
| `expr (void*)dlopen("/path/hook.dylib", 2)` | 运行时注入dylib |
| `watchpoint set variable globalVar` | 变量监控断点 |

12. **Frida Hook专项**：

**基础模板**：
```javascript
// ObjC方法Hook
Interceptor.attach(ObjC.classes.ClassName['- methodName'].implementation, {
  onEnter(args) { console.log('arg1:', ObjC.Object(args[2])); },
  onLeave(retval) { retval.replace(ptr(1)); } // 修改返回值
});
// Swift函数Hook(需先找到mangled name)
Interceptor.attach(Module.findExportByName(null, '$s...'), { ... });
// C函数Hook
Interceptor.attach(Module.findExportByName(null, 'CCCrypt'), {
  onEnter(args) { console.log('key:', Memory.readByteArray(args[3], 32)); }
});
```

**常用Frida操作**：
- `frida -n "AppName" -l hook.js` — 附加并注入脚本
- `frida-trace -n "AppName" -m "-[NS* *license*]"` — 自动追踪匹配方法
- `frida-ps -l` — 列出本地进程
- `ObjC.classes.ClassName.$methods` — 列出所有方法
- `ObjC.chooseSync(ObjC.classes.ClassName)` — 查找内存中的实例
- `Memory.scan(addr, size, pattern, { onMatch(addr,size){} })` — 内存模式搜索

13. **内存逆向专项**：

**内存搜索与Dump**：
- **搜索明文敏感数据**：用Frida的`Memory.scan`或LLDB的`memory find`搜索密码/Token/密钥
- **Dump解密后数据**：Hook加密函数(CCCrypt/AES/RSA)→在加密前捕获明文
- **对象图遍历**：`ObjC.chooseSync()`找到目标对象→遍历属性→追踪关联对象
- **堆分析**：Xcode Instruments > Allocations→查看内存分配→搜索敏感类名

**内存修改**：
- **修改变量值**：LLDB `memory write ADDR 0x01`或Frida `Memory.writeU8(addr, 1)`
- **修改函数返回值**：LLDB `register write x0 1`或Frida `retval.replace(ptr(1))`
- **Patch指令**：Frida `Memory.patchCode(addr, 4, code => { code.putRet(); })`
- **NOP指令**：用`\x1f\x20\x03\xd5`(arm64 NOP)覆盖检查指令

**内存取证流程**：
1. 识别目标函数(如`-[LicenseManager isActivated]`)
2. LLDB断点或Frida Hook→观察参数和返回值
3. 在内存中搜索相关字符串/数值
4. Dump相关内存区域保存为证据
5. 修改值验证假设→确认逻辑关系

### Phase 5: 二进制层分析

13. **静态反编译**：
    - IDA Pro / Ghidra / Hopper 反编译
    - 交叉引用定位关键函数
    - 控制流图分析
    - 常量特征匹配识别算法（AES S-box/SHA256 K值/CRC表）

14. **算法还原**：
    - 加密算法识别（特征常量/S盒/轮函数）
    - 自定义算法还原（伪代码→高级语言实现）
    - 密钥派生流程还原
    - 签名/校验算法还原

### Phase 6: 输出报告

```
## 逆向分析报告

### 目标信息
- 名称/版本/平台/架构
- 保护机制：签名/混淆/加壳/反调试

### 攻击面
| 输入点 | 类型 | 信任边界 | 风险等级 |

### 关键发现
| # | 类别 | 描述 | 证据 | 影响 | 利用难度 |

### 算法还原
| 算法名称 | 用途 | 类型 | 还原代码/伪代码 |

### 防护分析
| 防护类型 | 实现方式 | 绕过方案 | 验证状态 |

### 漏洞清单
| # | 类型 | 描述 | PoC | 影响 | CVSS |

### 分析过程记录
| 步骤 | 工具/命令 | 输出摘要 | 结论 |
```

### Phase 9: 设计逆向（架构/UI/业务逻辑还原）

18. **UI逆向**：
    - **视图层次分析**：Xcode View Debugger / `Accessibility Inspector` / Frida枚举UIWindow子视图
    - **布局还原**：截屏→标注尺寸/间距/字体/颜色→推断设计规范
    - **动画分析**：`CAAnimation`/`UIView.animate`参数Hook→还原动画曲线/时长/延迟
    - **资源提取**：从.app包提取图片/字体/Lottie JSON/颜色资源
    - **macOS专项**：`NSView hierarchy` / `NSWindow contentView` / SwiftUI `_printChanges()`

19. **架构逆向**：
    - **分层识别**：从类名/目录结构推断架构模式(MVC/MVVM/VIPER/Clean)
    - **依赖关系图**：`otool -L`查动态库 → class-dump查类依赖 → 绘制模块依赖图
    - **数据流追踪**：从UI事件→ViewModel/Controller→Service→Network/DB，还原完整数据流
    - **第三方库识别**：`strings`提取特征字符串 → 匹配已知SDK(Firebase/Amplitude/Sentry等)
    - **本地存储架构**：UserDefaults/Keychain/SQLite/Core Data/文件系统各存什么数据

20. **业务逻辑还原**：
    - **核心流程**：注册→登录→付费→核心功能→退出，每步的请求/响应/本地操作
    - **计费模型**：订阅/买断/消耗型？校验在客户端/服务端？离线能用吗？
    - **License机制**：激活流程→校验时机→离线容忍期→到期处理→防共享机制
    - **功能开关**：Feature Flag从哪获取？本地缓存策略？默认值？
    - **版本兼容**：API版本协商机制？强制更新逻辑？数据迁移策略？

21. **竞品技术分析**：
    - **技术栈识别**：原生/跨平台(Electron/Flutter/React Native)？用了什么框架？
    - **性能基线**：启动时间/内存占用/CPU使用/包大小/网络请求数
    - **安全对比**：加密强度/证书固定/代码混淆/反调试等级
    - **功能对比**：核心功能实现方式差异→找到可优化的点

## 审计优先级
外部输入 > 认证授权 > 加密实现 > 计费逻辑 > 文件操作 > 数据库 > 序列化 > 日志

## Linux/Windows 二进制分析流程

### Phase 0: 侦察（任何二进制第一步）
```bash
file target                          # 文件类型（ELF/PE/Mach-O）
checksec --file=target               # 保护机制（NX/ASLR/Canary/PIE/RELRO）
strings -n 8 target | head -50       # 关键字符串
readelf -h target                    # ELF头信息
readelf -S target                    # 段信息
ldd target                           # 动态库依赖（Linux）
nm -D target                         # 动态符号
```

### 文件类型决策树
```
文件类型？
├── ELF (Linux)
│   ├── stripped → 从strings/imports入手定位关键函数
│   ├── 动态链接 → 分析导入表确认库依赖
│   └── 静态链接 → strings找关键字符串→交叉引用
├── PE (Windows)
│   ├── .NET → dnSpy/ILSpy反编译（几乎等于源码）
│   ├── C/C++ → Ghidra/IDA反编译→分析WinAPI调用模式
│   └── 加壳 → DIE/Exeinfo识别壳→脱壳→重新分析
├── Mach-O (macOS/iOS) → 见上方Phase 3
└── 固件 (bin/img)
    ├── binwalk -Me firmware.bin 提取文件系统
    └── 找关键二进制→确认架构(ARM/MIPS)→Ghidra加载
```

### GDB 调试速查
```bash
gdb ./target
(gdb) info functions                 # 函数列表
(gdb) break main                     # 函数断点
(gdb) break *0x401234                # 地址断点
(gdb) run                            # 运行
(gdb) ni / si                        # 步过/步入
(gdb) x/20x $rsp                     # 查看栈（20个hex word）
(gdb) x/s 0x402000                   # 查看字符串
(gdb) info registers                 # 寄存器
(gdb) set $rax = 1                   # 修改寄存器（绕过检查）
(gdb) catch syscall write            # 系统调用断点
(gdb) vmmap                          # 内存映射（需pwndbg/peda/gef）
```

### 常见保护与绕过（ELF/PE）
| 保护 | 检测方法 | 绕过策略 |
|------|----------|----------|
| ASLR | checksec | 信息泄露基址 |
| NX | checksec | ROP chain |
| Canary | checksec | 泄露/覆盖TLS |
| PIE | checksec | 泄露.text基址 |
| RELRO | checksec | Full RELRO→无法覆GOT |
| UPX壳 | DIE/Exeinfo | `upx -d` |
| VMProtect | 特征识别 | 找OEP+dump |
| 控制流平坦化 | OLLVM特征 | 符号执行/手动还原 |

## Ghidra MCP 工具策略（有Ghidra MCP时优先使用）

| 任务 | MCP工具 | 备选 |
|------|---------|------|
| 列出函数 | `mcp__ghidra__list_functions` | `readelf -s` / `nm` |
| 反编译函数 | `mcp__ghidra__decompile_function` | IDA F5 / Hopper |
| 按地址反编译 | `mcp__ghidra__decompile_function_by_address` | — |
| 反汇编 | `mcp__ghidra__disassemble_function` | `objdump -d` |
| 交叉引用 | `mcp__ghidra__get_xrefs_to` / `get_xrefs_from` | IDA X |
| 字符串搜索 | `mcp__ghidra__list_strings` | `strings` |
| 导入/导出 | `mcp__ghidra__list_imports` / `list_exports` | `nm` |
| 段信息 | `mcp__ghidra__list_segments` | `readelf -l` |
| 类/命名空间 | `mcp__ghidra__list_classes` / `list_namespaces` | `class-dump` |
| 重命名 | `mcp__ghidra__rename_function` / `rename_variable` | — |
| 标注 | `mcp__ghidra__set_decompiler_comment` | — |

**Ghidra分析流程**：
1. `list_functions` → 定位main/关键函数名
2. `list_strings` → 找密码、URL、错误信息等线索
3. `decompile_function("main")` → 理解主逻辑
4. `get_xrefs_to(关键函数)` → 调用链追踪
5. `rename_function` / `set_decompiler_comment` → 标注分析结果

## 约束
- 所有操作在授权范围内执行，用户为持证安全研究员
- 每个发现必须有工具输出证据，禁止主观猜测（无证据=无法复现，安全研究要求可验证性）
- 临时文件/dump/capture即用即删
- 敏感数据(密钥/凭证)禁止明文存储在工作区（明文存储会被意外提交到Git或被其他工具扫描泄露）
- 分析过程可复现（记录完整命令和参数）
