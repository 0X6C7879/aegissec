from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from app.compat.skills import models as skill_models


class SkillGovernanceStatus(str, Enum):
    INCUBATING = "incubating"
    ACTIVE = "active"
    WATCH = "watch"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class SkillLayoutKind(str, Enum):
    FLAT = "flat"
    FAMILY_DIRECT = "family_direct"
    FAMILY_NESTED = "family_nested"


class ReferenceCostHint(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class SkillBodyParagraphKind(str, Enum):
    CORE_RULE = "core_rule"
    BACKGROUND = "background"
    EXAMPLE = "example"
    TEMPLATE = "template"
    REDUNDANT = "redundant"


@dataclass(slots=True)
class GovernanceLintIssue:
    level: str
    code: str
    message: str
    skill_id: str | None = None
    path: str | None = None
    details: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "skill_id": self.skill_id,
            "path": self.path,
            "details": self.details,
        }


@dataclass(slots=True)
class SkillDiscoveryIssue:
    relative_path: str
    reason: str

    def to_payload(self) -> dict[str, str]:
        return {"relative_path": self.relative_path, "reason": self.reason}


@dataclass(slots=True)
class GovernanceReferenceDocument:
    path: str
    relative_path: str
    when: str | None
    topics: list[str] = field(default_factory=list)
    cost_hint: ReferenceCostHint = ReferenceCostHint.UNKNOWN
    content: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "when": self.when,
            "topics": list(self.topics),
            "cost_hint": self.cost_hint.value,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class SkillRegistryEntry:
    skill_id: str
    path: str
    family: str | None
    owner: str
    version: str
    status: SkillGovernanceStatus
    description_tokens: int = 0
    body_tokens: int = 0
    reference_tokens: int = 0
    invocation_30d: int = 0
    route_collision_score: float = 0.0
    task_pass_rate: float = 0.0
    routing_pass_rate: float = 0.0
    obsolescence_score: float = 0.0
    last_verified_model: str | None = None
    last_verified_at: str | None = None
    depends_on: list[str] = field(default_factory=list)
    neighbors: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(slots=True)
class RoutingTestCase:
    case_id: str
    prompt: str
    expected_skill_id: str
    touched_paths: list[str] = field(default_factory=list)
    available_tools: list[str] = field(default_factory=list)
    current_prompt: str | None = None
    scenario_type: str | None = None


@dataclass(slots=True)
class TaskEvalCase:
    case_id: str
    prompt: str
    mode: str
    required_terms: list[str] = field(default_factory=list)
    reference_topics: list[str] = field(default_factory=list)
    format_terms: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(slots=True)
class GovernedSkill:
    governance_id: str
    family: str | None
    layout: SkillLayoutKind
    relative_path: str
    parsed_record: skill_models.ParsedSkillRecordData
    references: list[GovernanceReferenceDocument] = field(default_factory=list)

    @property
    def entry_file(self) -> str:
        return self.parsed_record.entry_file

    @property
    def directory_name(self) -> str:
        return self.parsed_record.directory_name


@dataclass(slots=True)
class SkillReductionSection:
    classification: SkillBodyParagraphKind
    text: str
    source_index: int
    duplicate_of: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "text": self.text,
            "source_index": self.source_index,
            "duplicate_of": self.duplicate_of,
        }


@dataclass(slots=True)
class SkillReductionResult:
    skill_id: str
    reduced_description: str
    reduced_body: str
    restored_snippets: list[str] = field(default_factory=list)
    original_description_tokens: int = 0
    reduced_description_tokens: int = 0
    original_body_tokens: int = 0
    reduced_body_tokens: int = 0
    sections: list[SkillReductionSection] = field(default_factory=list)
    deduplicated_reference_paths: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "skill_id": self.skill_id,
            "reduced_description": self.reduced_description,
            "reduced_body": self.reduced_body,
            "restored_snippets": list(self.restored_snippets),
            "original_description_tokens": self.original_description_tokens,
            "reduced_description_tokens": self.reduced_description_tokens,
            "original_body_tokens": self.original_body_tokens,
            "reduced_body_tokens": self.reduced_body_tokens,
            "sections": [section.to_payload() for section in self.sections],
            "deduplicated_reference_paths": list(self.deduplicated_reference_paths),
        }


@dataclass(slots=True)
class RoutingEvaluationResult:
    case_id: str
    expected_skill_id: str
    selected_skill_id: str | None
    passed: bool
    variant: str = "reduced"
    scenario_type: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "expected_skill_id": self.expected_skill_id,
            "selected_skill_id": self.selected_skill_id,
            "passed": self.passed,
            "variant": self.variant,
            "scenario_type": self.scenario_type,
        }


@dataclass(slots=True)
class TaskVariantResult:
    passed: bool
    matched_terms: list[str] = field(default_factory=list)
    missing_terms: list[str] = field(default_factory=list)
    selected_references: list[str] = field(default_factory=list)
    token_cost: int = 0
    format_passed: bool = True
    reference_usage_passed: bool = True
    failure_reasons: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "matched_terms": list(self.matched_terms),
            "missing_terms": list(self.missing_terms),
            "selected_references": list(self.selected_references),
            "token_cost": self.token_cost,
            "format_passed": self.format_passed,
            "reference_usage_passed": self.reference_usage_passed,
            "failure_reasons": list(self.failure_reasons),
        }


@dataclass(slots=True)
class TaskCaseEvaluationResult:
    skill_id: str
    case_id: str
    mode: str
    baseline: TaskVariantResult
    original: TaskVariantResult
    reduced: TaskVariantResult
    restore_rounds: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "skill_id": self.skill_id,
            "case_id": self.case_id,
            "mode": self.mode,
            "baseline": self.baseline.to_payload(),
            "original": self.original.to_payload(),
            "reduced": self.reduced.to_payload(),
            "restore_rounds": self.restore_rounds,
        }
