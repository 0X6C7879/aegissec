# SecKnowledge - Web & AI Security Testing Skill

📖 [中文](README.md)

> A security testing expert skill for Claude Code / Cursor that distills 88,636 real-world vulnerability cases, 5,600+ security research papers, and 150 AI security risks into an instantly accessible penetration testing knowledge base.

---

## Why This Skill?

When using Claude Code or Cursor for security testing, generic AI knowledge often falls short in depth and coverage. This Skill transforms your AI into a **seasoned security testing expert**:

- Given a target, it systematically enumerates attack surfaces and test cases
- When blocked by a WAF, it draws from 88,636 real bypass cases to suggest countermeasures
- For AI application testing, it covers 150 GAARM risks + OWASP LLM/Agent Top 10
- When you need payloads, it provides battle-tested cheat sheets

## Knowledge Sources

| Source | Scale | Content |
|--------|-------|---------|
| **WooYun Vulnerability DB** | 88,636 real vulnerabilities | SQL injection, XSS, command execution, file upload, logic flaws — real-world cases & bypass techniques |
| **Xianzhi Security Community** | 5,600+ security papers | L1-L4 Security Research Thinking Pyramid methodology |
| **GAARM Risk Matrix** | 150 AI security risks | From NSFOCUS AISS community, covering 6 security domains × 3 lifecycle stages |
| **OWASP Frameworks** | LLM Top 10 / Agentic AI Top 10 / WSTG | Latest 2025-2026 compliance mapping |

## Coverage

### Web Security (Traditional + Modern)

```
Injection Attacks    SQL Injection / XSS / Command Execution / XXE / Deserialization
Logic Flaws          Authorization Bypass / Payment Tampering / Password Reset / Race Conditions
File Security        File Upload / Path Traversal / SSRF / Information Disclosure
Modern Protocols     CORS / GraphQL / HTTP Smuggling / WebSocket / OAuth
Deployment           Supply Chain / Cloud Services / TLS / Containers / CI/CD
Framework Security   Fingerprinting → CVE Matching → PoC Verification (generic methodology)
```

### AI Security (6 Domains × 150 Risks)

```
AI App Security(34)       Prompt Injection / CoT Attacks / MCP Poisoning / Agent Exploitation
AI Model Security(42)     Jailbreak / Hallucination Abuse / Adversarial Samples / Model Theft
AI Data Security(32)      Prompt Leakage / Data Exfiltration / Inference Attacks / RAG Poisoning
AI Identity Security(23)  Role Escape / Permission Failures / Agent Impersonation / Session Hijacking
AI Infra Security(19)     Sandbox Escape / Container Escape / Supply Chain / DoS
Frontier Risks            MCP Tool Poisoning / Agent Worms / Skills Injection / Claude Code CVEs
```

### Core Methodologies

```
Xianzhi L1-L4          Attack Surface ID → Hypothesis Verification → Deep Exploitation → Defense Reversal
WooYun Essence         Vuln = Expected Behavior - Actual Behavior = Developer Assumptions ⊕ Attacker Input
GAARM Matrix           6 Security Domains × 3 Lifecycle Stages = Systematic AI Risk Coverage
OWASP Mapping          LLM01-10 / ASI01-10 / WSTG-* Compliance IDs
```

## File Structure

```
SKILL.md                              # Entry: quick-ref cards + decision tree + navigation
references/
├── web-injection.md                  # SQL Injection/XSS/Command Exec/XXE/Deserialization (906 lines)
├── web-logic-auth.md                 # AuthZ Bypass/Payment/Password Reset/Logic Flaws (582 lines)
├── web-file-infra.md                 # File Upload/Traversal/SSRF/Info Disclosure (632 lines)
├── web-modern-protocols.md           # CORS/GraphQL/HTTP Smuggling/WebSocket/OAuth (348 lines)
├── web-deployment-security.md        # Supply Chain/Cloud Deployment/Framework CVE Detection (449 lines)
├── ai-app-security.md                # 34 AI App Risks + Agent/MCP Frontier (2007 lines)
├── ai-model-security.md              # 42 Model Security Risks (2651 lines)
├── ai-data-security.md               # 32 Data Security Risks (1715 lines)
├── ai-identity-security.md           # 23 Identity Security Risks (1272 lines)
├── ai-baseline-security.md           # 19 Infra Risks + Container Escape Methodology (1177 lines)
├── gaarm-risk-matrix.md              # 150 AI Risk Index Table (158 lines)
├── testing-methodology.md            # Unified Testing Methodology (589 lines)
└── payloads.md                       # Web + AI Dual-Domain Payload Cheat Sheet (960 lines)
```

**Total**: 14 files, ~14,000 lines, 501KB

## Installation

### Claude Code

Clone this repo to Claude Code's skills directory:

```bash
git clone https://github.com/Pa55w0rd/secknowledge-skill.git ~/.claude/skills/secknowledge
```

### Cursor

Clone this repo to Cursor's skills directory:

```bash
git clone https://github.com/Pa55w0rd/secknowledge-skill.git ~/.cursor/skills/secknowledge
```

Once cloned, the AI will automatically load this Skill when you engage in security-related tasks.

## Usage Examples

### Scenario 1: Web Penetration Testing

```
User: Test target.com for SQL injection
AI:   [Auto-loads SKILL.md → web-injection.md → payloads.md]
      → Lists high-risk injection points, DB fingerprinting, WAF bypass techniques, full exploitation chain
```

### Scenario 2: AI Application Security Assessment

```
User: Test this chatbot's prompt injection defenses
AI:   [Auto-loads SKILL.md → ai-app-security.md → payloads.md]
      → Systematic testing: direct injection / indirect injection / MCP poisoning / Agent exploitation
```

### Scenario 3: Hybrid Application Attack Chains

```
User: This AI app has file upload and RAG features, how to test?
AI:   [Loads cross-layer attack chains]
      → Web layer (file upload bypass) → AI layer (RAG poisoning / indirect injection) → combined exploitation
```

### Scenario 4: Query Specific Risks

```
User: What is GAARM.0039?
AI:   [Consults gaarm-risk-matrix.md → ai-app-security.md]
      → Returns full attack overview, cases, risk analysis, mitigations
```

## Trigger Keywords

The following keywords automatically trigger Skill loading:

> vulnerability research, penetration testing, security audit, code review, security assessment, red team,
> CTF, SQL injection, XSS, command execution, file upload, SSRF, authorization bypass, logic flaws,
> prompt injection, jailbreak, MCP security, agent security, LLM security, sandbox escape,
> data leakage, model security, RAG poisoning, supply chain security

## Methodology Framework

```
User Request
│
├─ Web App ──→ Input params?  → Injection testing [web-injection.md]
│              File features? → Upload/Traversal  [web-file-infra.md]
│              Business logic?→ AuthZ/Payment     [web-logic-auth.md]
│              Modern protocols? → CORS/GraphQL/WS [web-modern-protocols.md]
│
├─ AI App ──→ Chat interface? → Prompt Injection/Jailbreak [ai-app-security.md]
│              Agent/MCP?     → Tool abuse/Poisoning       [ai-app-security.md]
│              Code execution?→ Sandbox escape             [ai-baseline-security.md]
│
├─ Deployment → Supply chain/Cloud/Framework CVE [web-deployment-security.md]
│
├─ Container/Sandbox → Escape/Persistence/Lateral movement [ai-baseline-security.md + payloads.md]
│
└─ Payloads → Cheat sheet [payloads.md]
```

## Acknowledgments & References

This Skill's knowledge system is built upon the following outstanding projects and communities:

| Project | Description |
|---------|-------------|
| [WooYun Legacy](https://github.com/tanweai/wooyun-legacy) | A Claude Code Skill curated by the Tanwei Security Research Team, containing 88,636 real vulnerability cases. This project's web security knowledge (injection, file operations, logic flaws, etc.) is distilled from this vulnerability database |
| [Xianzhi Security Research Methodology](https://github.com/tanweai/xianzhi-research) | L1-L4 meta-thinking methodology framework extracted from 5,621 security papers in the Xianzhi community. This project's four-layer thinking model and cross-domain attack chain thinking originate from this work |
| [AISS - NSFOCUS AI Security Smart Link Community](https://aiss.nsfocus.com/) | AI security knowledge base by NSFOCUS, providing the GAARM risk matrix with 150 AI security risk entries covering 6 security domains × 3 lifecycle stages |
| [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | 2025 edition — Top 10 risks for LLM applications |
| [OWASP Agentic AI Security Top 10](https://owasp.org/www-project-agentic-ai-security-initiative/) | 2026 edition — Top 10 risks for AI Agents |
| [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/) | v4.2 — Web Security Testing Guide with WSTG-* classification |

## Author

[Pa55w0rd](https://github.com/Pa55w0rd)

## Disclaimer

All content in this Skill is **for security research and defensive purposes only**. Please conduct security testing only with proper authorization and in compliance with local laws and regulations. All knowledge sources are from publicly available security communities and standard frameworks.

## License

MIT License

---

*Version: v1.0 | Author: Pa55w0rd | Knowledge Fusion: WooYun 88,636 cases × Xianzhi 5,600+ papers × GAARM 150 risks × OWASP LLM/ASI/WSTG*
