# AegisSec 集成 PayloadsAllTheThings 开发文档（README-first 全量覆盖版）

## 1. 文档目标

本文档用于指导将 **PayloadsAllTheThings（PATTT）** 全量纳入 `aegissec` 项目目录，并以 **README-first** 的方式接入到对话式渗透测试流程中。仓库地址：https://github.com/swisskyrepo/PayloadsAllTheThings。

这里的核心要求不是“把 PATTT 做成一个静态索引库”，而是：

1. **PATTT 全量文件进入项目目录**，作为本地知识仓。
2. **模型在运行时读到实际的 PATTT 原始 README / markdown 文件内容**，而不是只读预先切好的 payload 片段。
3. **所有 PATTT 类型都能被覆盖**，包括：
   - 标准章节目录（`README.md` + `Intruder/Images/Files`）
   - `README.md` + 多个补充 markdown 的章节
   - 只有 `README.md` 的章节
   - 没有 `README.md`、而是多个平铺 `.md` 文件的目录
4. **索引只负责定位文档，不替代原始文档。**
5. **模型最终看到的是被选中的原始 README / manual 原文内容**，并基于这些内容再提取候选 payload、绕过方式、辅助字典和相关说明。

---

## 2. 本版设计结论

### 2.1 结论

采用以下方案：

- 将 PATTT 全量 vendoring 到 `knowledge/pattt/repo/`。
- 构建一个 **README / markdown 目录清单（catalog）**，它只保存路径、结构、标题、哈希、别名和少量定位信息。
- 在运行时新增一个 **PATTT README Resolver**：
  - 先根据当前任务上下文选出候选家族；
  - 再**打开并读取**对应目录中的实际 `README.md` 或 `.md` 文档；
  - 再从这些实际文档中抽取候选 payload / bypass / exploit 说明。
- 在 AegisSec 现有 skills 的上下文组装阶段，把这些**原始文档内容**注入给模型，而不是只把预处理后的 payload 记录喂给模型。

### 2.2 设计原则

本方案遵循以下五条硬原则：

1. **README-first**
   - 对任一 payload family，优先读取它的 canonical README。
   - 索引和摘要只能做“文档路由”，不能替代 README 本体。

2. **全仓动态发现**
   - 不手工维护 payload 家族白名单。
   - 每次构建自动扫描 PATTT 仓库中的所有有效目录与 markdown 文件。

3. **路径驱动，不靠记忆**
   - 模型选择 payload 时，必须先拿到实际文件路径，再读取文件内容。
   - 不允许只依靠内部知识或仅靠预存摘要生成建议。

4. **覆盖优先于分片**
   - 不再把 PATTT 拆成多个静态知识 skill 作为主要入口。
   - 优先保证“任何 PATTT 家族都能被发现并读取到原始文档”。

5. **索引只做定位，不做知识替身**
   - 可以建 catalog、heading index、alias map、FTS。
   - 但运行时必须回源到实际 README / `.md` 文件。

---

## 3. PATTT 结构对接入方案的影响

PATTT 并不是单一格式仓库，而是混合结构仓库。接入方案必须顺着它的结构设计，不能假设所有内容都只是“一个 README + 一堆代码块”。

### 3.1 PATTT 的标准章节结构

PATTT 仓库 README 明确写明：每个章节通常包含以下文件或目录：

- `README.md`
- `Intruder`
- `Images`
- `Files`

因此，`README.md` 应被视为章节的**主入口文档**；而 `Intruder/Images/Files` 应作为**辅助资源**与其绑定，而不是被拆散成独立知识源。

### 3.2 目录形态并不统一

PATTT 当前仓库根目录下已经包含大量不同类型的 payload family，例如：

- `API Key Leaks`
- `Account Takeover`
- `Command Injection`
- `GraphQL Injection`
- `JSON Web Token`
- `Prompt Injection`
- `SQL Injection`
- `Server Side Request Forgery`
- `Upload Insecure Files`
- `Web Sockets`
- `XSS Injection`
- `XXE Injection`
- `XS-Leak`
- `Methodology and Resources`
- `CVE Exploits`

因此，接入时不能依赖人工维护一个“只支持常见几类”的路由列表，而必须做**自动发现**。

### 3.3 几种必须支持的目录模式

PATTT 当前至少存在以下四种模式，系统必须全部支持：

#### 模式 A：标准目录，含 `README.md` 与辅助资源

例如 `Server Side Request Forgery/`：

- `README.md`
- `SSRF-Advanced-Exploitation.md`
- `SSRF-Cloud-Instances.md`
- `Files/`
- `Images/`

这类目录的 `README.md` 是 canonical 文档，补充 `.md` 是子手册。

#### 模式 B：`README.md` + 多个专题手册

例如 `SQL Injection/`：

- `README.md`
- `MySQL Injection.md`
- `PostgreSQL Injection.md`
- `SQLite Injection.md`
- `MSSQL Injection.md`
- `Intruder/`
- `Images/`

又如 `XSS Injection/`：

- `README.md`
- `1 - XSS Filter Bypass.md`
- `2 - XSS Polyglot.md`
- `4 - CSP Bypass.md`
- `Intruders/`
- `Files/`
- `Images/`

这类目录必须先读 `README.md`，再按上下文补读专题手册。

#### 模式 C：只有 `README.md`

例如 `Prompt Injection/`。

这类目录不需要额外专题文件，直接把 `README.md` 作为 canonical 文档即可。

#### 模式 D：没有 `README.md`，而是平铺 `.md`

例如 `Methodology and Resources/`，其中直接平铺了大量 `.md` 文件，如云、容器、网络、Windows、Linux 等方法学文档。

这类目录不能因为“没有 README”就被漏掉；需要把每个 `.md` 文件都视为**独立可加载入口文档**。

---

## 4. 新的总体架构

### 4.1 目录结构

```text
./
├── knowledge/
│   └── pattt/
│       ├── repo/                         # PATTT 全量原始仓库
│       ├── catalog/
│       │   ├── families.json            # family 级目录清单
│       │   ├── docs.jsonl               # 文档级清单
│       │   ├── sections.jsonl           # 标题/section 级清单
│       │   ├── assets.jsonl             # Intruder/Files/Images 清单
│       │   ├── aliases.json             # 常用缩写与别名
│       │   └── build-meta.json          # commit、时间、统计信息
│       └── cache/
│           └── readme-cache/            # 运行时文件缓存（按 sha256）
│
├── scripts/
│   ├── sync_pattt.py                    # 同步 PATTT 仓库
│   ├── build_pattt_catalog.py           # 生成 catalog
│   ├── resolve_pattt_context.py         # 运行时文档定位与读取
│   ├── extract_pattt_candidates.py      # 从已加载文档中抽取候选 payload
│   └── validate_pattt_coverage.py       # 覆盖率校验
│
├── skills/
│   └── pattt-readme-loader/
│       └── SKILL.md                     # 仅负责何时调用 README Resolver
│
└── payloads.md
```

### 4.2 组件职责

#### 1) `sync_pattt.py`

负责把 PATTT 同步到本地项目目录。

职责：

- 首次拉取或更新 `knowledge/pattt/repo/`
- 记录源仓库 commit SHA
- 触发 catalog 重新构建

#### 2) `build_pattt_catalog.py`

负责扫描 PATTT 全仓并生成目录清单。

职责：

- 自动发现所有 top-level payload family
- 识别 canonical README
- 识别 child manuals
- 识别 `Intruder/Intruders/Files/Images`
- 解析 markdown 标题结构
- 生成 family/doc/section/asset 索引

#### 3) `resolve_pattt_context.py`

这是本方案的核心。

职责：

- 接收当前任务上下文
- 在 catalog 中定位最相关的 PATTT 文档路径
- **从磁盘读取实际 README / `.md` 原文**
- 返回 `ContextPack` 给 AegisSec 上下文注入层

#### 4) `extract_pattt_candidates.py`

职责：

- 从**已加载的原始文档内容**中提取 payload 候选
- 保留 `source_path`、`section_title`、`doc_kind`
- 供现有 request-builder / executor / analyzer 使用

#### 5) `pattt-readme-loader` skill

职责：

- 决定什么时候调用 `resolve_pattt_context.py`
- 不直接存 PATTT 全量知识
- 不替代原始文档读取

---

## 5. 覆盖契约（必须实现）

本节定义“什么叫真的覆盖了 PATTT 全部类型”。

### 5.1 自动发现规则

扫描 `knowledge/pattt/repo/` 根目录时：

- 忽略以下特殊目录或文件：
  - `.github`
  - `_template_vuln`
  - `_LEARNING_AND_SOCIALS`
  - dotfiles
  - 根目录下非知识性元文件（如 `LICENSE`、`mkdocs.yml`）
- 对其余所有目录执行 family 发现逻辑。

### 5.2 family 发现逻辑

对每个 top-level 目录 `D`：

1. 若 `D/README.md` 存在：
   - `README.md` 记为 `canonical_doc`
   - `D/*.md` 中除 `README.md` 外的文件记为 `child_docs`
   - `D/**/Intruder*`、`D/**/Files`、`D/**/Images` 记为 `assets`

2. 若 `D/README.md` 不存在，但 `D/*.md` 存在：
   - 将 `D/*.md` 中的每个 `.md` 记为独立 `canonical_doc`
   - 其 `family_id` 由目录名 + 文件名共同生成

3. 若 `D/README.md` 不存在，且没有任何 `.md`：
   - 视为异常目录
   - 构建阶段输出 warning；若该目录含 payload 辅助文件，则构建失败

### 5.3 文档归一化规则

每个被发现的文档都必须落入以下三类之一：

- `canonical`：章节主入口文档
- `child_manual`：canonical 之下的专题文档
- `standalone_manual`：没有 README 时直接作为入口的文档

### 5.4 覆盖完成定义

只有同时满足以下条件，才算“覆盖完成”：

1. 每个有效 top-level family 至少有一个 `canonical_doc` 或 `standalone_manual`。
2. 每个有效 `.md` 文件都能在 `docs.jsonl` 中找到一条记录。
3. 每个 `README.md` 文件都被标记为 `canonical`。
4. 每个 `canonical` 文档的标题结构都被解析到 `sections.jsonl`。
5. 每个 `Intruder/Intruders/Files/Images` 资源都被记录到 `assets.jsonl`。
6. 构建完成后，存在一份 `coverage_report`，明确列出：
   - 总 family 数
   - 总 doc 数
   - 总 canonical 数
   - 总 child manual 数
   - 总 standalone manual 数
   - 总 asset 数
   - 被忽略目录清单
   - 覆盖失败项清单

### 5.5 构建失败条件

以下任一情况发生，构建直接失败：

- 任一有效 top-level family 没有 entrypoint 文档
- 任一 `README.md` 未进入 catalog
- 任一 `.md` 文档未进入 `docs.jsonl`
- 任一目录既包含 payload 辅助资源又没有任何 entrypoint 文档
- 发现 catalog 哈希与磁盘原文不一致但未刷新

---

## 6. 构建阶段实现

### 6.1 PATTT 同步方式

推荐使用 `git subtree`：

```bash
git subtree add --prefix=knowledge/pattt/repo \
  https://github.com/swisskyrepo/PayloadsAllTheThings.git master --squash
```

更新：

```bash
git subtree pull --prefix=knowledge/pattt/repo \
  https://github.com/swisskyrepo/PayloadsAllTheThings.git master --squash
```

### 6.2 catalog 生成流程

执行：

```bash
python scripts/build_pattt_catalog.py
python scripts/validate_pattt_coverage.py
```

处理流程：

1. 扫描顶层目录
2. 判断目录模式（A/B/C/D）
3. 发现 canonical / child / standalone docs
4. 解析每个 markdown 的：
   - H1/H2/H3 标题
   - 代码块
   - 列表块
   - 文中对 `Files/Intruder/Images` 的引用
5. 生成 family/doc/section/asset catalog
6. 输出 coverage report

### 6.3 依赖建议

建议使用：

- `pathlib`
- `hashlib`
- `json`
- `sqlite3`（可选，用于 FTS）
- `markdown-it-py` 或 `mistune`

注意：

- FTS 只能作为“定位加速层”
- 不能把 FTS 命中的 snippet 直接当作最终知识源

### 6.4 family 级 catalog 结构

`knowledge/pattt/catalog/families.json`：

```json
[
  {
    "family_id": "server-side-request-forgery",
    "display_name": "Server Side Request Forgery",
    "root_dir": "knowledge/pattt/repo/Server Side Request Forgery",
    "layout": "readme+manuals",
    "canonical_doc": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
    "child_docs": [
      "knowledge/pattt/repo/Server Side Request Forgery/SSRF-Advanced-Exploitation.md",
      "knowledge/pattt/repo/Server Side Request Forgery/SSRF-Cloud-Instances.md"
    ],
    "assets": {
      "intruder": [],
      "files": [
        "knowledge/pattt/repo/Server Side Request Forgery/Files/..."
      ],
      "images": [
        "knowledge/pattt/repo/Server Side Request Forgery/Images/..."
      ]
    },
    "aliases": [
      "ssrf",
      "server side request forgery",
      "server-side request forgery"
    ],
    "sha256": "<family-hash>"
  }
]
```

### 6.5 文档级 catalog 结构

`docs.jsonl` 每行一条文档记录：

```json
{
  "doc_id": "server-side-request-forgery:readme",
  "family_id": "server-side-request-forgery",
  "path": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
  "kind": "canonical",
  "title": "Server-Side Request Forgery",
  "aliases": ["ssrf"],
  "heading_count": 22,
  "code_block_count": 31,
  "word_count": 4100,
  "sha256": "<doc-hash>"
}
```

### 6.6 section 级索引结构

`sections.jsonl` 的作用是“定位 README 的哪一段需要被读进来”。

```json
{
  "doc_id": "server-side-request-forgery:readme",
  "path": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
  "section_id": "ssrf:readme:h2:bypassing-filters",
  "heading_path": ["Server-Side Request Forgery", "Bypassing Filters"],
  "line_start": 320,
  "line_end": 420,
  "keywords": ["localhost", "ipv6", "dns rebinding", "redirect"],
  "sha256": "<section-hash>"
}
```

### 6.7 覆盖校验伪代码

```python
from pathlib import Path

IGNORE = {".github", "_template_vuln", "_LEARNING_AND_SOCIALS"}


def discover_top_dirs(repo_root: Path) -> list[Path]:
    result = []
    for p in repo_root.iterdir():
        if not p.is_dir():
            continue
        if p.name in IGNORE or p.name.startswith("."):
            continue
        result.append(p)
    return sorted(result)


def validate_coverage(repo_root: Path, docs_index: set[str]) -> list[str]:
    errors = []
    for family_dir in discover_top_dirs(repo_root):
        md_files = sorted(family_dir.glob("*.md"))
        has_assets = any((family_dir / name).exists() for name in ["Intruder", "Intruders", "Files", "Images"])
        if not md_files and has_assets:
            errors.append(f"{family_dir} has assets but no markdown entrypoint")
            continue
        for md in md_files:
            if str(md) not in docs_index:
                errors.append(f"missing doc index: {md}")
    return errors
```

---

## 7. 运行时的 README 读取与上下文注入

本节是本方案的关键。

### 7.1 输入信号

README Resolver 接收以下输入：

- 当前用户任务描述
- 当前目标类型（web/api/llm/upload/auth/cloud 等）
- 已有发现（如 family 猜测、响应特征、参数位置、技术栈）
- 任务阶段（枚举、验证、绕过、分析）
- 约束条件（如是否需要 OOB、是否不能改状态、是否只做低风险验证）

### 7.2 路由只负责找文件，不负责代替文件

运行时分两段：

#### 第一段：路由阶段

做这些事情：

- 根据别名、目录名、文档标题、标题关键词对 family 打分
- 选出最相关的 `family_id`
- 决定应该读取：
  - canonical README
  - 哪些 child manuals
  - 是否还需要对应的 `Intruder/Files`

这一段**不能**只返回 payload 文本。
必须返回：

- 文档路径
- 文档种类
- 读取理由
- 读取顺序

#### 第二段：回源读取阶段

做这些事情：

- 直接从 `knowledge/pattt/repo/...` 打开实际文件
- 把文件原文读入 `ContextPack`
- 再从原文中提取候选 payload / bypass / exploit 说明

结论：

> 索引命中只是“找到了要读哪份 README”，真正给模型看的必须是“README 原文”。

### 7.3 README 加载顺序

推荐顺序如下：

#### 规则 1：优先加载最相关 family 的 canonical 文档

- 若 family 有 `README.md`，先加载该 README 原文
- 若 family 没有 `README.md`，先加载最相关的 standalone `.md`

#### 规则 2：命中具体技术栈时，补读 child manuals

例如：

- 任务命中 `sql injection + mysql`
  - 先读 `SQL Injection/README.md`
  - 再读 `SQL Injection/MySQL Injection.md`

- 任务命中 `xss + csp bypass`
  - 先读 `XSS Injection/README.md`
  - 再读 `XSS Injection/4 - CSP Bypass.md`

- 任务命中 `ssrf + cloud metadata`
  - 先读 `Server Side Request Forgery/README.md`
  - 再读 `Server Side Request Forgery/SSRF-Cloud-Instances.md`

- 任务命中 `log4shell`
  - 先读 `CVE Exploits/README.md`
  - 再读 `CVE Exploits/Log4Shell.md`

#### 规则 3：只有在原文中出现明确引用时，才加载辅助资源

对于以下资源：

- `Intruder/Intruders`
- `Files`
- `Images`

处理方式：

- 默认不直接注入模型全文
- 只有当 README / child manual 明确引用它们，或当前任务要求字典/样本/辅助文件时，才按需读取
- 图片默认只保留路径，不注入；必要时再单独加载

#### 规则 4：低置信度时做多候选 fallback

如果 family 置信度不足：

- 同时读取 top 3 个候选 family 的 canonical 文档
- 每个 family 只加载 1 个入口文档
- 等模型或 analyzer 给出更多信号后，再二次精确加载 child manuals

### 7.4 上下文注入原则

注入给模型的不是 catalog 记录，而是：

```json
{
  "pattt_context": {
    "loaded_docs": [
      {
        "path": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
        "kind": "canonical",
        "reason": "family=ssrf; objective=validation",
        "content": "<actual markdown text>",
        "sha256": "<doc-hash>"
      },
      {
        "path": "knowledge/pattt/repo/Server Side Request Forgery/SSRF-Cloud-Instances.md",
        "kind": "child_manual",
        "reason": "cloud metadata signal matched",
        "content": "<actual markdown text>",
        "sha256": "<doc-hash>"
      }
    ],
    "payload_candidates": [
      {
        "source_path": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
        "section_title": "Bypassing Filters",
        "candidate_type": "payload",
        "text": "<extracted candidate from loaded doc>"
      }
    ]
  }
}
```

要求：

- `loaded_docs[].content` 必须来自实际磁盘文件读取
- `payload_candidates[]` 必须来自 `loaded_docs` 的原文解析结果
- 禁止先从旧缓存中拿 payload 再反推 source

### 7.5 大文档策略

若选中的 README 太长，不应直接把整份 PATTT 家族全文灌入模型。正确做法是：

1. 先完整读取 canonical README 到 resolver 内存
2. 再根据 section index 选出命中段落
3. 注入给模型时：
   - 至少包含 README 的标题结构 / summary
   - 必须包含命中的原文 section
4. 如模型还需要更多上下文，再增量读取相邻 section

重点：

- **可以裁剪注入量**
- **但不能跳过原始 README 读取步骤**

---

## 8. README 解析后的候选提取规则

候选提取只对**已经加载的原始文档**做，不对全仓直接做盲提取。

### 8.1 提取对象

从 `loaded_docs` 中提取：

- 代码块
- 行内 payload 示例
- 有序/无序列表中的候选项
- 标题与小节名称
- 指向 `Files/Intruder` 的引用

### 8.2 输出对象

统一输出：

```json
{
  "candidate_id": "ssrf-readme-bypass-filters-01",
  "candidate_type": "payload",
  "family_id": "server-side-request-forgery",
  "source_path": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
  "doc_kind": "canonical",
  "section_title": "Bypassing Filters",
  "text": "<candidate text>",
  "confidence": 0.86
}
```

### 8.3 关键限制

- 不能从未加载的 child manual 中提取候选
- 不能从 catalog 的 summary/snippet 直接生成候选
- 不能丢失 `source_path`

---

## 9. 与 AegisSec 技能链的集成方式

### 9.1 单入口，不做静态分片

本版不再把 PATTT 主体拆成多个固定 reference skill 作为主设计。

推荐做法：

- 只新增一个薄 skill：`pattt-readme-loader`
- 其职责是告诉 AegisSec：
  - 什么时候要查 PATTT
  - 查到以后必须读取实际 README / manual 原文

### 9.2 触发条件

当当前任务满足以下任一条件时，触发 README Resolver：

- 用户明确要求 payload / bypass / exploit candidate
- 当前任务已形成漏洞家族假设
- 当前阶段进入验证 / 绕过 / 定向枚举
- 当前 analyzer 需要从 PATTT 查找同家族技巧或专题手册

### 9.3 与现有执行 skills 的边界

PATTT 相关组件只负责：

- 找文档
- 读文档
- 从文档提取候选
- 返回 source-aware 的候选结果

PATTT 相关组件不负责：

- 直接发网络请求
- 自动执行高风险动作
- 替代你现有的受控 executor / analyzer

### 9.4 Skill 侧硬约束

`pattt-readme-loader/SKILL.md` 中必须写清楚：

- 选择 payload 前必须先调用 resolver
- resolver 返回的 `loaded_docs` 必须进入模型上下文
- 没有 `loaded_docs` 时，不要把 PATTT 当成已知来源使用
- 返回的建议必须带 `source_path`

---

## 10. 运行时 API 契约

### 10.1 `resolve_pattt_context.py`

建议输入：

```json
{
  "objective": "validation",
  "task_text": "need payload candidates for possible ssrf against cloud metadata endpoint",
  "family_hint": "ssrf",
  "tech_stack": ["aws"],
  "signals": ["metadata", "internal request", "url parameter"],
  "max_families": 3,
  "max_docs": 4
}
```

建议输出：

```json
{
  "families": [
    {
      "family_id": "server-side-request-forgery",
      "score": 0.94
    }
  ],
  "loaded_docs": [
    {
      "path": "knowledge/pattt/repo/Server Side Request Forgery/README.md",
      "kind": "canonical",
      "content": "<actual markdown text>"
    },
    {
      "path": "knowledge/pattt/repo/Server Side Request Forgery/SSRF-Cloud-Instances.md",
      "kind": "child_manual",
      "content": "<actual markdown text>"
    }
  ],
  "candidates": []
}
```

### 10.2 `extract_pattt_candidates.py`

输入：

```json
{
  "loaded_docs": ["...actual loaded docs..."],
  "objective": "validation"
}
```

输出：

```json
{
  "candidates": [
    {
      "source_path": "...",
      "section_title": "...",
      "candidate_type": "payload",
      "text": "..."
    }
  ]
}
```

---

## 11. 路由与文档选择算法

### 11.1 family 打分来源

family 排序建议使用混合打分：

- 别名命中：35%
- 文件名/目录名命中：20%
- section 关键词命中：20%
- 技术栈或产品名命中：15%
- 当前阶段权重：10%

### 11.2 别名机制

必须维护一份 `aliases.json`，解决安全缩写与目录名不一致的问题，例如：

```json
{
  "ssrf": ["server side request forgery", "server-side request forgery"],
  "sqli": ["sql injection"],
  "ssti": ["server side template injection"],
  "xxe": ["xxe injection"],
  "jwt": ["json web token"],
  "xss": ["xss injection", "cross site scripting"]
}
```

### 11.3 示例路由规则

#### 示例 1：SSRF

- family 命中：`Server Side Request Forgery`
- 首读：`Server Side Request Forgery/README.md`
- 若信号含 `cloud`, `metadata`, `aws`, `gcp`, `azure`
  - 追加：`SSRF-Cloud-Instances.md`
- 若信号含 `bypass`, `redirect`, `dns rebinding`, `parser discrepancy`
  - 追加：`SSRF-Advanced-Exploitation.md`

#### 示例 2：SQL Injection

- family 命中：`SQL Injection`
- 首读：`SQL Injection/README.md`
- 若信号含 `mysql`
  - 追加：`MySQL Injection.md`
- 若信号含 `postgres` 或 `postgresql`
  - 追加：`PostgreSQL Injection.md`
- 若信号含 `oracle`
  - 追加：`OracleSQL Injection.md`

#### 示例 3：XSS

- family 命中：`XSS Injection`
- 首读：`XSS Injection/README.md`
- 若信号含 `filter bypass`
  - 追加：`1 - XSS Filter Bypass.md`
- 若信号含 `polyglot`
  - 追加：`2 - XSS Polyglot.md`
- 若信号含 `csp`
  - 追加：`4 - CSP Bypass.md`

#### 示例 4：Prompt Injection

- family 命中：`Prompt Injection`
- 首读：`Prompt Injection/README.md`
- 无 child manual 时不再扩展

#### 示例 5：CVE

- family 命中：`CVE Exploits`
- 首读：`CVE Exploits/README.md`
- 若信号显式命中 `Log4Shell`
  - 追加：`CVE Exploits/Log4Shell.md`

#### 示例 6：方法学目录

- 命中：`Methodology and Resources`
- 若信号含 `aws`
  - 读取：`Cloud - AWS Pentest.md`
- 若信号含 `docker`
  - 读取：`Container - Docker Pentest.md`
- 若信号含 `kubernetes`
  - 读取：`Container - Kubernetes Pentest.md`

---

## 12. 缓存与一致性设计

### 12.1 允许缓存什么

允许缓存：

- family catalog
- docs index
- section index
- 文件哈希
- 原始 README 文本的进程内缓存或磁盘缓存

### 12.2 不允许缓存什么

不允许把以下内容作为唯一知识源长期使用：

- 旧版 payload 摘要
- 脱离原文的切片 cache
- 无 source_path 的候选 payload 列表

### 12.3 缓存失效规则

只要以下任一变化发生，必须重新读取原始文档：

- PATTT 仓库更新
- 文件 SHA256 变化
- canonical -> child 的映射变化
- section line range 变化

---

## 13. 测试与验收

### 13.1 单元测试

必须至少有以下测试：

1. `test_every_valid_top_level_dir_is_discovered`
2. `test_every_markdown_file_is_indexed`
3. `test_every_readme_is_marked_canonical`
4. `test_methodology_flat_md_collection_is_supported`
5. `test_intruder_and_intruders_are_both_supported`
6. `test_assets_are_bound_to_family`
7. `test_source_path_is_preserved`
8. `test_changed_file_invalidates_cache`

### 13.2 集成测试

至少做以下六个案例：

1. `ssrf + cloud metadata`
   - 必须读到 `Server Side Request Forgery/README.md`
   - 若上下文命中 cloud，必须能继续读到 `SSRF-Cloud-Instances.md`

2. `sql injection + mysql`
   - 必须读到 `SQL Injection/README.md`
   - 必须能继续读到 `MySQL Injection.md`

3. `xss + csp`
   - 必须读到 `XSS Injection/README.md`
   - 必须能继续读到 `4 - CSP Bypass.md`

4. `prompt injection`
   - 必须读到 `Prompt Injection/README.md`

5. `log4shell`
   - 必须读到 `CVE Exploits/README.md`
   - 必须能继续读到 `Log4Shell.md`

6. `aws pentest methodology`
   - 必须能够在 `Methodology and Resources/` 下选中对应 `.md`

### 13.3 验收标准

只有满足以下条件才算验收通过：

- PATTT 全量进入项目目录
- catalog 覆盖所有有效 family 与所有 `.md`
- 模型收到的 PATTT 上下文中包含实际 README / manual 原文内容
- 每个候选 payload 都能追溯到 `source_path`
- 当 family 命中变化时，系统会重新回源读取新的 README，而不是沿用旧摘要

---

## 14. 具体实施步骤

### 第一步：将 PATTT vendoring 到项目目录

执行：

```bash
git subtree add --prefix=knowledge/pattt/repo \
  https://github.com/swisskyrepo/PayloadsAllTheThings.git master --squash
```

### 第二步：实现 catalog builder

新增：

- `scripts/build_pattt_catalog.py`
- `scripts/validate_pattt_coverage.py`

要求：

- family 自动发现
- README / manual / asset 自动归类
- section 索引自动生成

### 第三步：实现 runtime resolver

新增：

- `scripts/resolve_pattt_context.py`
- `scripts/extract_pattt_candidates.py`

要求：

- 输入当前任务上下文
- 输出已读取的原始文档内容
- 输出来源可追踪的候选项

### 第四步：把 resolver 接到 AegisSec 的上下文组装阶段

要求：

- 在需要 payload 知识时先调用 resolver
- 将 `loaded_docs[].content` 注入模型上下文
- 再让现有 skills 做请求构造、验证和分析

### 第五步：新增 `pattt-readme-loader` skill

要求：

- skill 只做“何时调用 resolver”的调度说明
- 不承载 PATTT 全量知识正文
- 不替代实际文件读取

### 第六步：写测试并跑覆盖率校验

执行：

```bash
python scripts/build_pattt_catalog.py
python scripts/validate_pattt_coverage.py
pytest tests/test_pattt_catalog.py -q
pytest tests/test_pattt_runtime.py -q
```

---

## 15. 最终方案的一句话定义

**PATTT 在 AegisSec 中应被实现为“本地全量原始知识仓 + README-first 动态解析与回源读取器”，而不是“预切片 payload 数据库”或“静态分片知识 skill 集合”。**

这套方案解决的核心不是“如何把 PATTT 摘要化”，而是：

- 如何保证 **所有 PATTT 类型都能被发现**
- 如何保证 **模型最终读到的是真实 README / manual 原文**
- 如何保证 **payload 建议始终可追溯到 PATTT 的具体文件路径与章节**

这才是你要求的“全量覆盖 PATTT，并确保模型根据实际应用情况读到合适 README.md”的工程落地方式。
