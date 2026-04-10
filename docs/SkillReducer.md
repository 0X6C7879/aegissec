# SkillReducer：aegissec 多 Skill 体系优化开发文档

## 1. 文档目标

本文档定义 aegissec 项目的多 Skill 优化方案，并将其落地为一套可执行的开发计划、目录规范、评测方法、CI 流水线与里程碑安排。

本方案的核心目标不是单纯“压缩 Skill 文本”，而是系统性解决以下问题：

1. Skill 过多导致的路由冲突。
2. SKILL.md 过长导致的上下文注入成本过高。
3. references 冗余导致的额外 token 消耗。
4. 低价值、过时、重复 Skill 长期留存在候选池。
5. 缺少统一评测，导致“看起来更短”但实际效果退化。

本方案参考 SkillReducer 论文的方法论，但针对 aegissec 的工程场景做了产品化和平台化扩展，目标是把当前 Skill 集合升级为一套可治理、可评测、可退役、可持续演进的 Skill 平台。

---

## 2. 适用范围

本方案适用于 aegissec 项目中所有具备以下特征的 Skills：

- 存在独立的 description / SKILL.md / references 结构。
- 会被自动路由或候选触发。
- 具有一定上下文注入成本。
- 存在功能重叠、历史包袱、质量波动或维护负担。

本方案不要求一次性重构全部 Skill，可按优先级分批实施。

---

## 3. 优化原则

### 3.1 路由层优先

先优化 description，再优化正文。因为多 Skill 项目的主要问题通常先出在“选错 Skill”而不是“Skill 不会做”。

### 3.2 控制平面与知识平面分离

SKILL.md 只保留核心规则、决策分支与流程控制；背景说明、案例、模板和大段资料移入 references。

### 3.3 按需加载优先于全量注入

所有非必需内容必须支持 progressive disclosure，避免默认加载整个 Skill 资料包。

### 3.4 评测先行

任何压缩、重写、删减、合并、退役都必须经过路由评测和任务评测，不允许仅凭主观判断上线。

### 3.5 小步快跑

优先实施低风险高收益动作：reference 去重、description 补全、Skill 资产盘点。正文精简和 Skill 退役放在后续批次。

---

## 4. 目标状态

优化完成后，aegissec 的 Skill 系统应达到以下状态：

1. 每个 Skill 都有统一的元数据登记。
2. 每个 Skill 的 description 都具备清晰触发能力。
3. 每个 SKILL.md 都只保留最小可执行核心。
4. references 被拆分为按主题加载的辅助材料。
5. 所有变更均经过自动化评测。
6. 无效、过时、重复 Skill 能进入 watch / deprecated / retired 生命周期。
7. CI 能自动发现冗余、冲突、回归和预算超标问题。

---

## 5. 当前问题建模

将当前 Skill 体系的问题建模为四类：

### 5.1 路由问题

表现：
- description 缺失、过短或过长。
- 多个 Skill 的 description 语义高度重叠。
- 触发词泛化，导致候选池碰撞。
- 同类任务下不同 Skill 轮流误触发。

### 5.2 正文问题

表现：
- SKILL.md 中混入大量背景介绍、示例和模板。
- 核心执行规则占比过低。
- 长 Skill 正文被整体注入，造成 token 开销。
- 某些示例实际上承担了隐藏规范角色。

### 5.3 references 问题

表现：
- 同一内容在 body 和 references 重复出现。
- references 文件过大、颗粒度过粗。
- 没有明确“什么时候读取”。
- 运行时倾向于全读而不是按需读。

### 5.4 生命周期问题

表现：
- 已过时 Skill 继续留在候选池中。
- 长期无调用 Skill 未清理。
- 新老 Skill 功能重叠但无人合并。
- 缺少 owner 与版本责任机制。

---

## 6. 总体架构

建议将 aegissec 的 Skill 平台重构为以下五层：

### 6.1 Skill Registry 层

负责统一登记 Skill 元数据、成本、质量和生命周期。

### 6.2 Routing 层

负责 description 规范化、候选选择、冲突检测、近邻区分与 shadow skill 对抗验证。

### 6.3 Execution Core 层

即精简后的 SKILL.md，仅保留核心操作规则、决策逻辑与何时加载 references。

### 6.4 Reference Layer

按主题切分辅助材料，并附带 when/topics/cost_hint 元信息，供运行时按需读取。

### 6.5 Evaluation + Governance 层

负责 lint、压缩、评测、反馈修复、生命周期推进、退役与看板指标。

---

## 7. 目录规范

建议仓库结构升级为：

```text
skills/
  family-detection/
    event-correlation/
      SKILL.md
      scripts/
      references/
      assets/
    alert-triage/
      SKILL.md
      scripts/
      references/
  family-reporting/
    incident-summary/
      SKILL.md
      references/
registry/
  skill-registry.yaml
  routing-testset.yaml
  task-eval-set/
ci/
  lint_skills.py
  reduce_skill.py
  eval_routing.py
  eval_task.py
  report_metrics.py
reports/
  latest/
    routing_report.json
    task_report.json
    registry_metrics.json
```

说明：

- `skills/` 采用 family 分组，减少全局平铺冲突。
- `registry/` 保存元数据与评测样本。
- `ci/` 存放自动化检查与评测脚本。
- `reports/` 输出最新质量报告。

---

## 8. Skill Registry 设计

### 8.1 每个 Skill 必填字段

建议采用 `registry/skill-registry.yaml` 维护，字段如下：

```yaml
- skill_id: event-correlation
  family: family-detection
  owner: sec-platform
  version: 1.3.0
  status: active
  description_tokens: 78
  body_tokens: 412
  reference_tokens: 1830
  invocation_30d: 124
  route_collision_score: 0.31
  task_pass_rate: 0.92
  routing_pass_rate: 0.97
  obsolescence_score: 0.18
  last_verified_model: gpt-5.4-thinking
  last_verified_at: 2026-04-10
  depends_on: []
  neighbors:
    - alert-triage
    - incident-summary
```

### 8.2 生命周期状态

允许以下状态：

- `incubating`：新建 Skill，尚未大规模验证。
- `active`：稳定使用中。
- `watch`：命中率低、冲突高或收益下降，需要观察。
- `deprecated`：已明确不建议继续新增使用。
- `retired`：从主候选池移除，仅保留历史归档。

### 8.3 watch / deprecated 触发条件

满足以下任一条件时，进入 `watch`：

1. 30 天调用量显著低于同类 Skill。
2. route collision score 高。
3. routing pass rate 或 task pass rate 下降。
4. no-skill 与 with-skill 的质量差异趋近于零。
5. 与邻近 Skill 功能高度重合。

满足以下任意条件时，可进入 `deprecated`：

1. 连续两个版本周期处于 `watch`。
2. 被新 Skill 完整覆盖。
3. 依赖的技术栈或业务场景已废弃。
4. owner 确认不再维护。

---

## 9. Description 优化规范

### 9.1 description 统一格式

每个 Skill description 必须表达三件事：

1. 这个 Skill 做什么。
2. 什么情况下应该触发。
3. 它与相邻 Skill 的区分信号是什么。

推荐模板：

```text
<核心能力>. Use when the request involves <触发条件1>, <触发条件2>, or <特定输入/产物/协议>. Especially relevant for <差异化标识>.
```

### 9.2 禁止项

description 中禁止出现：

- 营销式措辞。
- 过长 feature list。
- 与触发无关的背景介绍。
- 冗长案例。
- 模糊表述，如 “many kinds of tasks”。

### 9.3 自动补全过程

对于 description 缺失或明显失效的 Skill，采用以下提取逻辑：

1. 从 SKILL.md 提取核心能力句。
2. 从示例任务中抽取主要触发条件。
3. 从脚本名、协议名、文件类型、产物类型中抽取唯一标识。
4. 生成初始 description。
5. 进入 routing eval 验证。
6. 若误触发或漏触发，则 selective restore 关键短语。

### 9.4 Description 预算

建议：

- 最短不少于 20 tokens。
- 常规目标 40 到 100 tokens。
- 超过 120 tokens 时默认触发 lint 警告。

---

## 10. SKILL.md 精简规范

### 10.1 SKILL.md 允许保留的内容

SKILL.md 应只包含：

1. 核心任务目标。
2. 顺序流程或条件分支。
3. 关键判断标准。
4. 输出约束。
5. 何时读取哪类 reference。
6. 必须运行的脚本与调用顺序。

### 10.2 SKILL.md 应剥离的内容

下列内容应迁移出 SKILL.md：

- 长背景知识。
- 大段 API / schema 摘录。
- 超过最小必要量的示例。
- 完整模板正文。
- FAQ。
- 重复性的术语解释。

### 10.3 Body 五分类

所有正文段落在优化时需要落到以下五类之一：

- `core_rule`：必须保留在 SKILL.md 的规则。
- `background`：迁移到 references。
- `example`：迁移到 references/examples.md。
- `template`：迁移到 references/templates.md。
- `redundant`：删除。

### 10.4 处理 example-as-specification

若某段示例不仅是示例，而且承担了隐含规范功能，则不得直接移除。可采用以下方式之一：

1. 将其缩减为最小规范样例，继续保留在 core。
2. 改写为明确规则，再将完整样例迁移到 reference。
3. 为其建立自动化测试样本，确保迁移后行为不回归。

### 10.5 Body 预算

建议：

- 普通 Skill：SKILL.md 目标控制在 150–400 行。
- 重型 Skill：不超过 500 行。
- 若超过 500 行，必须强制拆分 references。

---

## 11. Reference 设计规范

### 11.1 Reference 分类

建议将 references 固化为以下几类：

- `background.md`：背景知识。
- `examples.md`：案例与样例。
- `templates.md`：输出模板。
- `schema.md`：结构、字段、协议或规则表。
- `faq.md`：常见异常与边界条件。

### 11.2 Reference 元信息

每个 reference 文件顶部增加轻量说明：

```yaml
when: 当任务涉及字段映射、协议细节或异常处理时读取
topics: [schema, mapping, protocol]
cost_hint: medium
```

### 11.3 运行时读取规则

运行时应遵循以下顺序：

1. 先读取 SKILL.md。
2. 根据任务目标判断是否需要额外信息。
3. 仅打开相关 topic 的 reference。
4. 禁止默认全量读取所有 reference。

### 11.4 去重规则

必须检查以下重复：

- body 与 reference 重复。
- reference 与 reference 重复。
- family 内多个 Skill 之间重复。

重复内容优先保留一份主副本，其他位置改为显式引用或删除。

---

## 12. 优化流水线设计

建议构建如下 build-time pipeline：

```text
lint -> reduce -> route_eval -> task_eval -> feedback_restore -> pack -> publish
```

### 12.1 lint 阶段

负责静态检查：

- description 是否缺失。
- description 是否过长或过短。
- SKILL.md 是否超过预算。
- 是否存在 example file / placeholder 未清理。
- body/reference 是否重复。
- references 是否缺失元信息。
- owner / version / status 是否缺失。

lint 失败时直接阻断进入 reduce。

### 12.2 reduce 阶段

负责结构化压缩：

1. description 清洗与补全。
2. body 五分类。
3. references 去重与切分。
4. 为 reference 添加 when/topics/cost_hint。
5. 生成 reduced 版本。

### 12.3 route_eval 阶段

验证 Skill 是否还能被正确选中：

1. 与近邻 Skill 对比。
2. 引入 shadow skill 干扰样本。
3. 测试原始请求集的命中率。
4. 输出 precision / recall / confusion matrix。

### 12.4 task_eval 阶段

对每个 Skill 至少设计 5 个任务：

- 3 个 core-only 任务。
- 2 个 needs-reference 任务。

执行三组对照：

- `D`：不带 Skill。
- `A`：原始 Skill。
- `C`：压缩后的 Skill。

验收标准：

- `C` 不得明显差于 `A`。
- 若 `C` 优于或等于 `A` 且注入成本更低，则通过。
- 若 `C` 低于 `A`，进入 feedback_restore。

### 12.5 feedback_restore 阶段

当 route_eval 或 task_eval 失败时，不回滚整个 Skill，而是做选择性恢复：

1. 找出失败任务依赖的信息片段。
2. 将对应片段从 references 提升回 core，或补回 description 关键语义。
3. 重新跑 route_eval / task_eval。
4. 限制最多两轮恢复，避免无限迭代。

### 12.6 pack / publish 阶段

通过评测后：

1. 输出优化报告。
2. 更新 registry 指标。
3. 生成最终 Skill 包。
4. 发布到候选池。

---

## 13. 路由评测设计

### 13.1 近邻集构建

每个 Skill 至少维护 3–5 个近邻 Skill，用于检测冲突。近邻定义方式：

- 同 family。
- 输入类型相近。
- 产物类型相近。
- 工具链相近。

### 13.2 Shadow Skill 设计

为每个 Skill 构造一个“看起来像，但实际功能不同”的对抗样本，用于测试 description 是否过泛。

例如：

- `incident-summary` 的 shadow skill 可为 `incident-root-cause-analysis`。
- `alert-triage` 的 shadow skill 可为 `alert-suppression-policy`。

### 13.3 路由验收标准

每个 Skill 的路由评测需要达到：

- recall >= 0.95
- precision >= 0.95
- 与最近邻的混淆率持续下降

若达不到，则该 Skill 不能进入发布流程。

---

## 14. 任务评测设计

### 14.1 测试集来源

测试任务集建议来自三类来源：

1. 历史真实调用。
2. 维护者手工编写的关键场景。
3. 围绕边界条件构造的对抗任务。

### 14.2 评分维度

任务评测至少检查：

- 任务完成率。
- 输出正确性。
- 输出格式合规性。
- 是否调用了必要 reference。
- 是否遗漏关键约束。
- token 成本。

### 14.3 回归判定

若压缩后版本出现以下任一问题，判定回归：

- 关键步骤缺失。
- 输出格式错误。
- 决策依据明显不足。
- 需 reference 的任务未正确读取 reference。
- 路由成功但执行失败。

---

## 15. 指标体系

建议在 `reports/latest/registry_metrics.json` 中持续输出以下指标：

### 15.1 规模指标

- Skill 总数。
- active / watch / deprecated / retired 数量。
- family 数量。

### 15.2 成本指标

- description token p50 / p95。
- body token p50 / p95。
- reference token p50 / p95。
- 单次平均注入 token。
- 平均 reference 加载个数。

### 15.3 质量指标

- routing precision / recall。
- task pass rate。
- regression count。
- selective restore 触发率。

### 15.4 治理指标

- 30 天未调用 Skill 数量。
- route collision top 10。
- obsolescence top 10。
- 重复内容占比。

### 15.5 建议验收目标

第一阶段可采用以下目标：

- description token p50 降低 40%。
- 单次 Skill 注入 token p50 降低 30%。
- routing precision / recall >= 95%。
- 每任务平均 reference 加载数 < 2。
- watch / deprecated 机制开始生效。

---

## 16. 开发分期

### Phase 1：基础治理与低风险收益

目标：先建立观测与约束。

范围：

1. 建立 Skill Registry。
2. 增加 lint。
3. 清洗 description。
4. 做 reference 去重。
5. 统计调用量、冲突率、token 成本。

交付物：

- `registry/skill-registry.yaml`
- `ci/lint_skills.py`
- `reports/latest/registry_metrics.json`
- 第一版 description 优化结果

验收：

- 所有 active Skill 都有 owner、status、version。
- 80% 以上 Skill 具备规范 description。
- reference 重复显著下降。

### Phase 2：正文精简与按需加载

目标：减少默认注入成本。

范围：

1. body 五分类。
2. 拆分 background/examples/templates/schema。
3. 引入 reference 元信息。
4. 实现 progressive disclosure。

交付物：

- `ci/reduce_skill.py`
- 若干 reduced skill 试点版本
- reference 切分规范

验收：

- 试点 Skill 注入成本下降。
- task pass rate 无显著下降。
- needs-reference 任务可稳定按需读取。

### Phase 3：自动评测与反馈恢复

目标：让压缩和上线可自动验证。

范围：

1. route_eval 自动化。
2. task_eval 自动化。
3. feedback_restore。
4. D / A / C 报告输出。

交付物：

- `ci/eval_routing.py`
- `ci/eval_task.py`
- `reports/latest/routing_report.json`
- `reports/latest/task_report.json`

验收：

- 所有试点 Skill 都有自动评测报告。
- selective restore 能修复主要失败样本。

### Phase 4：平台化与生命周期治理

目标：从“技能集合”升级为“技能平台”。

范围：

1. family 化重组。
2. obsolete skill 识别。
3. watch / deprecated / retired 机制上线。
4. 发布准入规则纳入 CI。

交付物：

- 新版 skills 目录结构
- 生命周期策略
- 下线与归档流程

验收：

- 重复 Skill 数量下降。
- 候选池更稳定。
- watch 与 retired 流程可执行。

---

## 17. 实施角色分工

### 17.1 Skill Owner

负责：

- 提供真实任务样本。
- 审核 description 与 core rules。
- 判定是否允许退役或合并。

### 17.2 Platform Owner

负责：

- 维护 registry。
- 落地 lint / reduce / eval pipeline。
- 维护看板和预算阈值。

### 17.3 Evaluator

负责：

- 编写 routing / task 测试集。
- 审核压缩后是否存在隐性能力退化。

### 17.4 Release Owner

负责：

- 控制发布准入。
- 管理 active / watch / deprecated / retired 状态切换。

---

## 18. 风险与应对

### 风险 1：description 变短后误触发增加

应对：

- 保留差异化标识。
- 强制跑近邻与 shadow skill 评测。
- 使用 selective restore 回填关键短语。

### 风险 2：正文压缩后能力下降

应对：

- 不做暴力截断。
- 采用五分类精简。
- 识别 example-as-specification。

### 风险 3：reference 拆分后运行时读不到

应对：

- 在 SKILL.md 明确写出何时读什么。
- 给 reference 增加 when/topics 元信息。
- 针对 needs-reference 任务单独评测。

### 风险 4：退役过早导致历史场景失效

应对：

- 先进入 watch，再 deprecated。
- 保留 retired 归档与回滚能力。
- 区分不同模型层级的 Skill 保留策略。

### 风险 5：团队不维护 registry

应对：

- 将 owner/version/status 设为 CI 必填项。
- 未登记 Skill 不允许发布。

---

## 19. 第一批推荐实施项

若当前资源有限，优先做以下四项：

1. 建 `skill-registry.yaml`。
2. 为所有 active Skill 清洗 description。
3. 做 body/reference 重复检测。
4. 对 top 20 高频 Skill 做 route_eval + task_eval 试点。

这四项具备最低改造风险和最高观测价值，可快速建立后续平台化基础。

---

## 20. 发布准入规则

任何 Skill 发生以下改动之一时，必须重新通过评测：

- description 修改。
- SKILL.md 结构性删减。
- references 重构。
- family 迁移。
- 生命周期状态变更。

发布前必须满足：

1. lint 全通过。
2. route_eval 达标。
3. task_eval 达标。
4. registry 指标已更新。
5. owner 审核通过。

---

## 21. 总结

aegissec 当前需要的不是继续增加更多 Skill，而是建立一套围绕 Skill 的治理系统。

本方案将 Skill 优化拆解为：

- 路由层优化
- 正文核心化
- reference 按需加载
- 自动化评测
- 生命周期治理

最终目标是让 Skill 从“堆积的提示资产”升级为“可观测、可验证、可裁剪、可退役的工程化能力单元”。

---

## 22. 后续建议

建议下一步直接补齐以下三个仓库文件：

1. `registry/skill-registry.yaml` 初版模板。
2. `ci/lint_skills.py` 的检查规则定义。
3. 单个 Skill 的“精简前 / 精简后”改造示例。

若继续推进，可在本文档基础上再补一份 `SkillReducer-Implementation-Checklist.md`，供研发按周执行。

