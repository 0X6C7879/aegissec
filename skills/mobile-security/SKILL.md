---
name: mobile-security
description: 移动安全与渗透测试技能 - Android/iOS安全评估、Frida Hook、SSL Pinning绕过、Root/越狱检测绕过、漏洞挖掘、防护对抗、OWASP MASTG。当你涉及移动应用安全测试、Android/iOS渗透、Frida Hook、SSL Pinning、Root检测绕过、漏洞挖掘、红队攻击、防护对抗时必须使用此技能。即使用户只是说"测试这个APP"或"绕过检测"，也应触发。
---

# 移动安全与渗透测试技能 (Mobile Security & Penetration Testing)

## 快速规则（日常开发时自动加载，只需读到这里）

> **[渗透核心清单]** ① 信息收集→攻击面枚举→漏洞发现→利用验证→报告，严格按阶段推进 ② 每个漏洞必须有PoC证明，"理论上可能"不算发现 ③ 最小化影响原则：验证漏洞存在即可，不扩大破坏
> **[OPSEC三禁]** ❌明文存储/传输提取的凭证 ❌在非授权环境执行exploit ❌不清理临时文件和调试痕迹
> **[测试铁律]** 所有操作在授权范围内，用户为持证安全研究员。测试仅在测试设备/模拟器上执行

渗透测试时，强制遵守：
1. **授权先行**：确认测试范围和授权，未授权的目标不碰
2. **证据驱动**：每个发现必须有工具输出/截图/PoC佐证
3. **最小影响**：验证漏洞存在即可，不执行破坏性操作
4. **OPSEC纪律**：敏感数据禁明文存储，临时文件即用即删
5. **完整记录**：关键步骤记录命令和输出，确保可复现

---

## 漏洞挖掘思维模型（每个功能点都问）

| 类别 | 核心问题 | 检查方法 |
|------|----------|----------|
| 白嫖 | 计费逻辑在哪执行？客户端还是服务端？ | Hook计费函数/修改请求参数/重放支付回调 |
| 越权 | 资源ID可预测吗？改ID能访问他人数据？ | 枚举ID/水平越权/垂直越权测试 |
| 绕过 | 限制在哪层实施？客户端限制=无限制 | 修改请求绕过验证/频率/次数/VIP检查 |
| 泄露 | 错误响应/日志/调试接口暴露什么？ | Fuzz异常输入/查找调试端点/读日志文件 |
| 注入 | 哪些输入可控？能注入到哪里？ | SQL/命令/模板/日志/Header注入测试 |
| 重放 | 请求能否重放？有时效/签名/序号校验吗？ | 捕获→延时重放→观察是否成功 |
| 降级 | 能否强制使用旧版本/弱加密/HTTP明文？ | 降级攻击/协议回退/算法替换 |

## 防护识别与绕过

| 防护类型 | 检测方式 | 绕过思路 |
|----------|----------|----------|
| 调试检测 | ptrace/sysctl/isatty/断点检测 | Hook检测函数返回"正常"值 |
| 环境检测 | 越狱/Root/模拟器/Frida端口检测 | 隐藏特征文件/Hook检测API |
| 完整性校验 | 代码签名/文件hash/内存校验 | 运行时patch校验函数/替换hash |
| 代码混淆 | OLLVM/名称混淆/控制流平坦化 | 从字符串/常量/系统调用入手定位 |
| 证书固定 | SSL Pinning/公钥固定 | Hook SSL验证函数/替换证书 |
| 反Hook | 检测Frida/Substrate/swizzle | 使用Stalker替代Interceptor/内联hook |

**通用对抗原则**：
- 检测类防护→定位检测函数→使其返回"正常"值
- 混淆类防护→从字符串/常量/系统调用入手定位关键逻辑
- 通信保护→运行时拦截加密前/解密后的明文数据
- 遇到未知防护→先观察行为特征→对比已知防护模式→逐步验证假设

---

## Android 安全测试

### 静态分析流程
```bash
# APK解包与反编译
apktool d APP.apk -o output/          # 资源+smali反编译
jadx -d output/ APP.apk               # DEX→Java反编译
unzip APP.apk -d extracted/           # 直接解压

# 信息收集
aapt dump badging APP.apk             # 包信息/权限/组件
keytool -printcert -jarfile APP.apk   # 签名证书信息

# Manifest审计要点
# - exported=true的Activity/Service/Receiver/Provider → 组件暴露
# - android:debuggable="true" → 可调试
# - android:allowBackup="true" → 数据泄露
# - networkSecurityConfig → 明文流量/证书固定配置
# - 过度权限 / 自定义权限保护等级

# 敏感信息搜索
grep -rn "API_KEY\|SECRET\|password\|token" output/
grep -rn "http://" output/            # 明文HTTP
grep -rn "10\.\|192\.168\.\|172\." output/  # 内网地址
```

### 数据存储检查
| 位置 | 路径 | 风险 |
|------|------|------|
| SharedPreferences | `/data/data/pkg/shared_prefs/` | 明文存储敏感数据 |
| SQLite | `/data/data/pkg/databases/` | 未加密数据库 |
| 外部存储 | `/sdcard/` | 全局可读 |
| Keystore | Android Keystore API | 评估使用方式 |
| 日志 | `logcat` | 敏感信息泄露 |

### Frida Hook模板（Java层）

```javascript
Java.perform(function() {
    // Hook普通方法
    var cls = Java.use("com.target.ClassName");
    cls.methodName.implementation = function(arg1, arg2) {
        console.log("[*] arg1: " + arg1 + ", arg2: " + arg2);
        var ret = this.methodName(arg1, arg2);
        console.log("[*] return: " + ret);
        return ret; // 或修改返回值
    };

    // Hook重载方法
    cls.methodName.overload("java.lang.String", "int").implementation = function(s, i) {
        return this.methodName(s, i);
    };

    // Hook构造函数
    cls.$init.overload("java.lang.String").implementation = function(s) {
        console.log("[*] new ClassName(" + s + ")");
        this.$init(s);
    };

    // 枚举类的所有方法
    var methods = cls.class.getDeclaredMethods();
    methods.forEach(function(m) { console.log(m.toString()); });
});
```

### Root检测绕过
```javascript
Java.perform(function() {
    // RootBeer绕过
    var RootBeer = Java.use("com.scottyab.rootbeer.RootBeer");
    RootBeer.isRooted.implementation = function() { return false; };

    // 通用文件检测绕过
    var File = Java.use("java.io.File");
    File.exists.implementation = function() {
        var path = this.getAbsolutePath();
        if (path.indexOf("su") !== -1 || path.indexOf("Superuser") !== -1 ||
            path.indexOf("magisk") !== -1) {
            return false;
        }
        return this.exists();
    };
});
```

### SSL Pinning绕过（Android）
```javascript
Java.perform(function() {
    // OkHttp3 CertificatePinner
    var CertificatePinner = Java.use("okhttp3.CertificatePinner");
    CertificatePinner.check.overload("java.lang.String", "java.util.List").implementation = function(hostname, peerCertificates) {
        console.log("[*] SSL Pinning bypassed for: " + hostname);
    };

    // TrustManager替换
    var X509TrustManager = Java.use("javax.net.ssl.X509TrustManager");
    var TrustManagerImpl = Java.registerClass({
        name: "com.custom.TrustManager",
        implements: [X509TrustManager],
        methods: {
            checkClientTrusted: function(chain, authType) {},
            checkServerTrusted: function(chain, authType) {},
            getAcceptedIssuers: function() { return []; }
        }
    });
});
// 推荐工具: frida-android-unpinning / objection
```

---

## iOS 安全测试

### 静态分析流程
```bash
# IPA解包
unzip APP.ipa -d extracted/
# 二进制分析
file Payload/APP.app/APP               # 架构
otool -L Payload/APP.app/APP           # 动态库依赖
class-dump Payload/APP.app/APP > dump.h # ObjC头文件
strings Payload/APP.app/APP | grep -iE 'key|password|token|secret|api'

# Info.plist审计
plutil -p Payload/APP.app/Info.plist   # URL Scheme/ATS配置
codesign -dv --entitlements :- Payload/APP.app  # 权限
```

### 数据存储检查
| 位置 | 说明 | 风险 |
|------|------|------|
| Keychain | `security find-generic-password` | 评估ACL配置 |
| NSUserDefaults | `~/Library/Preferences/*.plist` | 明文敏感数据 |
| CoreData/SQLite | `~/Library/Application Support/` | 未加密数据库 |
| 缓存/快照 | `Library/Caches/` `Snapshots/` | 敏感UI截图 |
| 剪贴板 | UIPasteboard | 跨应用数据泄露 |

### 脱壳
- `frida-ios-dump` — Frida方式dump解密IPA
- `CrackerXI+` — 越狱设备一键脱壳
- `bfdecrypt` — 另一种Frida脱壳方案

### Frida Hook模板（ObjC层）
```javascript
// ObjC方法Hook
var hook = ObjC.classes.ClassName["- methodName:"];
Interceptor.attach(hook.implementation, {
    onEnter: function(args) {
        console.log("[*] arg: " + ObjC.Object(args[2]));
    },
    onLeave: function(retval) {
        console.log("[*] ret: " + ObjC.Object(retval));
        retval.replace(ptr(1)); // 修改返回值
    }
});

// 越狱检测绕过
Interceptor.attach(ObjC.classes.NSFileManager["- fileExistsAtPath:"].implementation, {
    onEnter: function(args) {
        var path = ObjC.Object(args[2]).toString();
        if (path.indexOf("cydia") !== -1 || path.indexOf("substrate") !== -1) {
            this.shouldBypass = true;
        }
    },
    onLeave: function(retval) {
        if (this.shouldBypass) retval.replace(ptr(0));
    }
});
```

### SSL Pinning绕过（iOS）
```javascript
// NSURLSession SSL Pinning Bypass
var NSURLSessionConfiguration = ObjC.classes.NSURLSessionConfiguration;
Interceptor.attach(NSURLSessionConfiguration['- setTLSMinimumSupportedProtocol:'].implementation, {
    onEnter: function(args) { /* allow all */ }
});
// 推荐工具: SSL Kill Switch 2 / objection
```

---

## OWASP MASTG 移动安全检查项

| 类别 | 关键检查 | Android | iOS |
|------|----------|---------|-----|
| 存储 | 敏感数据明文 | SharedPrefs/SQLite/External | Keychain/NSUserDefaults/CoreData |
| 加密 | 硬编码密钥 | 代码/资源/assets搜索 | 二进制/plist搜索 |
| 认证 | 本地认证绕过 | BiometricPrompt Hook | LAContext Hook |
| 网络 | SSL Pinning | NetworkSecurityConfig | ATS/自定义实现 |
| 平台 | 组件暴露 | exported=true组件 | URL Scheme/Universal Links |
| 代码 | 反逆向 | ProGuard/R8/Root检测 | 越狱检测/混淆 |
| 隐私 | 数据收集 | 权限/SDK追踪 | 权限/SDK追踪 |
| 日志 | 日志泄露 | `logcat \| grep -i password` | `os_log`/`NSLog` |
| 备份 | 数据泄露 | allowBackup=true | iTunes备份未加密 |
| 剪贴板 | 敏感数据 | ClipboardManager | UIPasteboard |

---

## 渗透测试工具速查

| 工具 | 用途 | 关键用法 |
|------|------|----------|
| **Burp Suite** | Web/API渗透 | Repeater重放/Intruder爆破/Scanner扫描 |
| **Charles Proxy** | HTTP/HTTPS抓包 | 安装根证书→SSL Proxying→查看明文 |
| **mitmproxy** | 命令行抓包+脚本 | `mitmproxy --mode regular`→脚本修改请求 |
| **Frida** | 运行时Hook | `frida -n APP -l hook.js` |
| **objection** | 移动安全框架 | `objection -g APP explore` |
| **Drozer** | Android组件测试 | `drozer console connect` |
| **apktool** | APK解包 | `apktool d APP.apk` |
| **jadx** | DEX反编译 | `jadx -d output/ APP.apk` |
| **class-dump** | ObjC头文件导出 | `class-dump BINARY > dump.h` |
| **nmap** | 端口扫描 | `nmap -sV -p- TARGET` |

## 安全评估报告模板

```
## 移动应用安全评估报告

### 应用信息
| 属性 | 值 |
|------|------|
| 应用名 | ... |
| 包名 | com.xxx.xxx |
| 平台 | Android/iOS |
| 版本 | ... |

### 漏洞清单
| # | 类别 | 问题 | 严重性 | 位置/类 | PoC |

### 数据存储审计
| 位置 | 内容 | 加密 | 风险 |

### 通信安全
| 检查项 | 状态 | 备注 |

### 防护评估
| 防护类型 | 实现方式 | 绕过难度 | 验证状态 |

### 修复建议
（按OWASP MASTG分类）
```

## 约束
- 所有操作在授权范围内执行，用户为持证安全研究员
- 每个漏洞必须有PoC/工具输出证据，禁止主观猜测
- 测试仅在测试设备/模拟器上执行
- 临时文件/dump/capture即用即删
- 敏感数据(密钥/凭证)禁止明文存储在工作区
- SSL Pinning绕过/Root检测绕过仅用于安全测试，非攻击目的
- 分析过程可复现（记录完整命令和参数）
