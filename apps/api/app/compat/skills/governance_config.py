from __future__ import annotations

from dataclasses import dataclass

DESCRIPTION_MIN_TOKENS = 20
DESCRIPTION_TARGET_MIN_TOKENS = 40
DESCRIPTION_TARGET_MAX_TOKENS = 100
DESCRIPTION_WARN_MAX_TOKENS = 120

BODY_TARGET_MAX_LINES = 400
BODY_HARD_MAX_LINES = 500
BODY_TARGET_MAX_TOKENS = 320

ROUTING_PASS_THRESHOLD = 0.95
TASK_PASS_THRESHOLD = 0.95
MAX_RESTORE_ROUNDS = 2
REFERENCE_MAX_SELECTED = 2
REFERENCE_MAX_TOKEN_BUDGET = 2048
REFERENCE_LOAD_TARGET = 2.0

REFERENCE_GLOB = "**/*.md"
REFERENCE_REQUIRED_FIELDS = ("when", "topics", "cost_hint")
REQUIRED_TASK_CASE_SPLIT = {"core-only": 3, "needs-reference": 2}

RESERVED_DIRECT_CHILDREN = frozenset(
    {
        "assets",
        "data",
        "docs",
        "examples",
        "fixtures",
        "knowledge",
        "references",
        "scripts",
        "tests",
    }
)

BACKGROUND_HINTS = (
    "背景",
    "overview",
    "概述",
    "introduction",
    "why",
    "motivation",
    "history",
)
EXAMPLE_HINTS = (
    "示例",
    "example",
    "payload",
    "case",
    "场景",
    "案例",
)
TEMPLATE_HINTS = (
    "template",
    "模板",
    "boilerplate",
    "占位",
    "placeholder",
    "{{",
    "}}",
    "<insert",
)
CORE_RULE_HINTS = (
    "must",
    "必须",
    "should",
    "应当",
    "流程",
    "步骤",
    "when to use",
    "when not to use",
    "渐进加载规则",
    "core workflow",
)
PLACEHOLDER_TEXT_HINTS = (
    "todo",
    "fixme",
    "placeholder",
    "lorem ipsum",
    "your-skill-name",
    "your skill name",
    "replace me",
)
PLACEHOLDER_FILE_HINTS = (
    "placeholder",
    "todo",
)


@dataclass(frozen=True, slots=True)
class GovernanceThresholds:
    description_min_tokens: int = DESCRIPTION_MIN_TOKENS
    description_target_min_tokens: int = DESCRIPTION_TARGET_MIN_TOKENS
    description_target_max_tokens: int = DESCRIPTION_TARGET_MAX_TOKENS
    description_warn_max_tokens: int = DESCRIPTION_WARN_MAX_TOKENS
    body_target_max_lines: int = BODY_TARGET_MAX_LINES
    body_hard_max_lines: int = BODY_HARD_MAX_LINES
    body_target_max_tokens: int = BODY_TARGET_MAX_TOKENS
    routing_pass_threshold: float = ROUTING_PASS_THRESHOLD
    task_pass_threshold: float = TASK_PASS_THRESHOLD
    max_restore_rounds: int = MAX_RESTORE_ROUNDS
    reference_max_selected: int = REFERENCE_MAX_SELECTED
    reference_max_token_budget: int = REFERENCE_MAX_TOKEN_BUDGET
    reference_load_target: float = REFERENCE_LOAD_TARGET


DEFAULT_THRESHOLDS = GovernanceThresholds()
