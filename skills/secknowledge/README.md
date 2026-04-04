# SecKnowledge - Web与AI安全测试知识技能

📖 [English](README_EN.md)

> 一个为 Claude Code / Cursor 打造的安全测试专家技能（Skill），将 88,636 个真实漏洞案例、5,600+ 篇安全研究文档、150 条 AI 安全风险、OWASP LLM/ASI/WSTG和常用 200+安全测试用例浓缩为可即时调用的渗透测试知识库。

---

## 为什么需要这个 Skill？

当你使用 Claude Code 或 Cursor 进行安全评估时，AI 的通用知识往往不够专业和系统。这个 Skill 让 AI 变成一个**经验丰富的安全测试专家**：

- 给出一个目标，它能系统化地列出攻击面和测试用例
- 遇到 WAF 拦截，它能从 88,636 个真实绕过案例中提供对策
- 测试 AI 应用，它能覆盖 150 条 GAARM 风险 + OWASP LLM/Agent Top 10
- 需要 Payload，它能提供经过实战验证的完整速查库

## 知识来源

| 来源 | 规模 | 内容 |
|------|------|------|
| **WooYun 漏洞库** | 88,636 条真实漏洞 | SQL注入、XSS、命令执行、文件上传、逻辑漏洞等实战案例与绕过技巧 |
| **先知安全社区** | 5,600+ 篇安全文档 | L1-L4 安全研究思维金字塔方法论 |
| **GAARM 风险矩阵** | 150 条 AI 安全风险 | 来自 AISS 绿盟大模型安全智链社区，覆盖6大安全域×3生命周期 |
| **OWASP 三大框架** | LLM Top 10 / Agentic AI Top 10 / WSTG | 2025-2026 最新版合规映射 |

## 覆盖范围

### Web 安全（传统 + 现代）

```
注入攻击        SQL注入 / XSS / 命令执行 / XXE / 反序列化
逻辑漏洞        越权 / 支付篡改 / 密码重置 / 条件竞争
文件安全        文件上传 / 路径遍历 / SSRF / 信息泄露
现代协议        CORS / GraphQL / HTTP走私 / WebSocket / OAuth
部署安全        供应链 / 云服务 / TLS / 容器 / CI/CD
框架安全        指纹识别→CVE匹配→PoC验证 通用方法论
```

### AI 安全（6大安全域 × 150条风险）

```
AI应用安全(34)   Prompt注入 / CoT攻击 / MCP投毒 / Agent利用
AI模型安全(42)   越狱 / 幻觉滥用 / 对抗样本 / 模型窃取
AI数据安全(32)   Prompt泄露 / 数据窃取 / 推断攻击 / RAG投毒
AI身份安全(23)   角色逃逸 / 权限失控 / Agent伪造 / 会话劫持
AI基座安全(19)   沙箱逃逸 / 容器逃逸 / 供应链 / 拒绝服务
前沿风险         MCP工具投毒 / Agent蠕虫 / Skills注入 / Claude Code CVE
```

### 核心方法论

```
先知 L1-L4       攻击面识别 → 假设验证 → 深度利用 → 防御反推
WooYun 本质公式   漏洞 = 预期行为 - 实际行为 = 开发者假设 ⊕ 攻击者输入
GAARM 矩阵       6安全域 × 3生命周期 = 系统化AI风险覆盖
OWASP 映射       LLM01-10 / ASI01-10 / WSTG-* 合规编号
```

## 文件结构

```
SKILL.md                              # 入口：速查卡片 + 决策树 + 导航
references/
├── web-injection.md                  # SQL注入/XSS/命令执行/XXE/反序列化 (906行)
├── web-logic-auth.md                 # 越权/支付/密码重置/逻辑漏洞 (582行)
├── web-file-infra.md                 # 文件上传/遍历/SSRF/信息泄露 (632行)
├── web-modern-protocols.md           # CORS/GraphQL/HTTP走私/WebSocket/OAuth (348行)
├── web-deployment-security.md        # 供应链/云部署/框架CVE检测 (449行)
├── ai-app-security.md                # 34条AI应用风险 + Agent/MCP前沿 (2007行)
├── ai-model-security.md              # 42条模型安全风险 (2651行)
├── ai-data-security.md               # 32条数据安全风险 (1715行)
├── ai-identity-security.md           # 23条身份安全风险 (1272行)
├── ai-baseline-security.md           # 19条基座安全 + 容器逃逸方法论 (1177行)
├── gaarm-risk-matrix.md              # 150条AI风险索引表 (158行)
├── testing-methodology.md            # 统一测试方法论 (589行)
└── payloads.md                       # Web+AI双域Payload速查库 (960行)
```

**总计**: 14个文件，约 14,000 行，501KB

## 安装

### Claude Code

将本仓库克隆到 Claude Code 的 skills 目录：

```bash
git clone https://github.com/Pa55w0rd/secknowledge-skill.git ~/.claude/skills/secknowledge
```

### Cursor

将本仓库克隆到 Cursor 的 skills 目录：

```bash
git clone https://github.com/Pa55w0rd/secknowledge-skill.git ~/.cursor/skills/secknowledge
```

克隆完成后，AI 会在你进行安全相关操作时自动加载此 Skill。

## 使用示例

### 场景1：Web渗透测试

```
用户: 对 target.com 进行SQL注入测试
AI: [自动加载 SKILL.md → web-injection.md → payloads.md]
    → 列出高危注入点、数据库指纹识别、WAF绕过技巧、完整利用链
```

### 场景2：AI应用安全评估

```
用户: 测试这个chatbot的Prompt注入防护
AI: [自动加载 SKILL.md → ai-app-security.md → payloads.md]
    → 系统化测试直接注入/间接注入/MCP投毒/Agent利用
```

### 场景3：混合应用攻击链

```
用户: 这个AI应用有文件上传和RAG功能，怎么测试？
AI: [加载跨层攻击链]
    → Web层(文件上传绕过) → AI层(RAG投毒/间接注入) → 组合利用
```

### 场景4：查询特定风险

```
用户: GAARM.0039 是什么风险？
AI: [查阅 gaarm-risk-matrix.md → ai-app-security.md]
    → 返回完整的攻击概述、案例、风险分析、缓解措施
```

## 触发关键词

以下关键词会自动触发 Skill 加载：

> 漏洞挖掘、渗透测试、安全审计、代码审计、安全评估、红队攻防、CTF、
> SQL注入、XSS、命令执行、文件上传、SSRF、越权、逻辑漏洞、
> Prompt注入、越狱、MCP安全、Agent安全、LLM安全、沙箱逃逸、
> 数据泄露、模型安全、RAG投毒、供应链安全

## 方法论框架

```
用户请求
│
├─ Web应用 ──→ 输入参数? → 注入测试 [web-injection.md]
│              文件功能? → 上传/遍历 [web-file-infra.md]
│              业务逻辑? → 越权/支付 [web-logic-auth.md]
│              现代协议? → CORS/GraphQL/WS [web-modern-protocols.md]
│
├─ AI应用 ──→ 对话接口? → Prompt注入/越狱 [ai-app-security.md]
│              Agent/MCP? → 工具滥用/投毒 [ai-app-security.md]
│              代码执行? → 沙箱逃逸 [ai-baseline-security.md]
│
├─ 部署环境 → 供应链/云/框架CVE [web-deployment-security.md]
│
├─ 容器沙箱 → 逃逸/持久化/横向移动 [ai-baseline-security.md + payloads.md]
│
└─ Payload → 速查库 [payloads.md]
```

## 致谢与参考

本 Skill 的知识体系建立在以下优秀项目和社区之上：

| 项目 | 说明 |
|------|------|
| [WooYun Legacy](https://github.com/tanweai/wooyun-legacy) | 88,636 个真实漏洞案例的 Claude Code Skill，由探微杜渐安全研究团队整理。本项目的 Web 安全知识（注入、文件、逻辑漏洞等）从该漏洞库中提炼 |
| [先知安全研究方法论](https://github.com/tanweai/xianzhi-research) | 从先知社区 5,621 篇安全文档中提炼的 L1-L4 元思考方法论框架。本项目的四层思维模型和跨域攻击链思维源于此 |
| [AISS 绿盟大模型安全智链社区](https://aiss.nsfocus.com/) | 绿盟科技出品的 AI 安全知识库，提供了 GAARM 风险矩阵的 150 条 AI 安全风险条目，覆盖 6 大安全域 × 3 生命周期阶段 |
| [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | 2025 版 LLM 应用安全 Top 10 风险 |
| [OWASP Agentic AI Security Top 10](https://owasp.org/www-project-agentic-ai-security-initiative/) | 2026 版 AI Agent 安全 Top 10 风险 |
| [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/) | v4.2 Web 安全测试指南，WSTG-* 分类标准 |

## 作者

[Pa55w0rd](https://github.com/Pa55w0rd)

## 免责声明

本 Skill 中的所有内容**仅供安全研究与防御参考**。请在获得合法授权后进行安全测试，遵守当地法律法规。知识来源均来自公开的安全社区和标准框架。

## 许可证

MIT License

---

*版本: v1.0 | 作者: Pa55w0rd | 知识融合: WooYun 88,636案例 × 先知 5,600+文档 × GAARM 150风险 × OWASP LLM/ASI/WSTG × 常用 200+安全测试用例*
