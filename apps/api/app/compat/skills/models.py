from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.db.models import CompatibilityScope, CompatibilitySource, SkillRecordStatus


class SkillSourceKind(str, Enum):
    FILESYSTEM = "filesystem"
    LEGACY_COMMAND_DIRECTORY = "legacy_command_directory"
    BUNDLED = "bundled"
    MCP = "mcp"


class CompiledSkillExecutionMode(str, Enum):
    REFERENCE_ONLY = "reference_only"


class SkillPromptShellExpansionKind(str, Enum):
    INLINE = "inline"
    FENCED = "fenced"


class SkillCandidateRole(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    REFERENCE = "reference"
    REJECTED = "rejected"


@dataclass(slots=True)
class SkillScanRoot:
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str
    source_kind: SkillSourceKind = SkillSourceKind.FILESYSTEM
    root_label: str | None = None
    enabled: bool = True
    placeholder: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveredSkillFile:
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str
    directory_name: str
    entry_file: str
    relative_path: str = ""
    source_kind: SkillSourceKind = SkillSourceKind.FILESYSTEM
    root_label: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SkillSourceIdentity:
    source_kind: SkillSourceKind
    source: CompatibilitySource
    scope: CompatibilityScope
    source_root: str
    relative_path: str
    fingerprint: str

    @property
    def dedup_key(self) -> tuple[str, str, str, str]:
        return (
            self.source_kind.value,
            self.source_root.casefold(),
            self.relative_path.casefold(),
            self.fingerprint,
        )


@dataclass(slots=True)
class ParsedSkillFrontmatter:
    name: str
    description: str
    compatibility: list[str]
    metadata: dict[str, object]
    parameter_schema: dict[str, object]
    raw_frontmatter: dict[str, object]
    aliases: list[str] = field(default_factory=list)
    user_invocable: bool | None = None
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str | None = None
    activation_paths: list[str] = field(default_factory=list)
    when_to_use: str | None = None
    context_hint: str | None = None
    agent: str | None = None
    effort: str | None = None
    validation_error: str | None = None


@dataclass(slots=True)
class ParsedSkillRecordData:
    id: str
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str
    directory_name: str
    entry_file: str
    name: str
    description: str
    compatibility: list[str]
    metadata: dict[str, object]
    parameter_schema: dict[str, object]
    raw_frontmatter: dict[str, object]
    status: SkillRecordStatus
    enabled: bool
    error_message: str | None
    content_hash: str
    last_scanned_at: datetime
    aliases: list[str] = field(default_factory=list)
    user_invocable: bool | None = None
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str | None = None
    activation_paths: list[str] = field(default_factory=list)
    when_to_use: str | None = None
    context_hint: str | None = None
    agent: str | None = None
    effort: str | None = None
    source_identity: SkillSourceIdentity | None = None


@dataclass(slots=True)
class CompiledSkill:
    identity: SkillSourceIdentity
    skill_id: str
    name: str
    directory_name: str
    entry_file: str
    description: str
    content: str
    compatibility: list[str] = field(default_factory=list)
    parameter_schema: dict[str, object] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    user_invocable: bool | None = None
    argument_hint: str | None = None
    activation_paths: list[str] = field(default_factory=list)
    invocable: bool = True
    dynamic: bool = False
    when_to_use: str | None = None
    context_hint: str | None = None
    agent: str | None = None
    effort: str | None = None
    loaded_from: str | None = None
    shell_enabled: bool = True
    execution_mode: CompiledSkillExecutionMode = CompiledSkillExecutionMode.REFERENCE_ONLY
    prepared_prompt: str = ""
    prepared_invocation: PreparedSkillInvocation | None = None

    @property
    def is_conditional(self) -> bool:
        return bool(self.activation_paths)


@dataclass(slots=True)
class SkillInvocationRequest:
    arguments: dict[str, object] = field(default_factory=dict)
    workspace_path: str | None = None
    touched_paths: list[str] = field(default_factory=list)
    session_id: str | None = None


@dataclass(slots=True)
class SkillInvocationContext:
    skill_directory: str
    shell_enabled: bool
    substitution_values: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None


@dataclass(slots=True)
class SkillPromptShellExpansion:
    kind: SkillPromptShellExpansionKind
    command: str
    original_text: str
    line_start: int
    line_end: int
    shell_allowed: bool
    approval_required: bool = True
    status: str = "pending"
    reason: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind.value,
            "command": self.command,
            "original_text": self.original_text,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "shell_allowed": self.shell_allowed,
            "approval_required": self.approval_required,
            "status": self.status,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(slots=True)
class SkillInvocationPendingAction:
    action_type: str
    status: str
    payload: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "action_type": self.action_type,
            "status": self.status,
            "payload": dict(self.payload),
        }


@dataclass(slots=True)
class PreparedSkillInvocation:
    request: SkillInvocationRequest
    context: SkillInvocationContext
    prompt_text: str
    shell_expansions: list[SkillPromptShellExpansion] = field(default_factory=list)
    pending_actions: list[SkillInvocationPendingAction] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "request": {
                "arguments": dict(self.request.arguments),
                "workspace_path": self.request.workspace_path,
                "touched_paths": list(self.request.touched_paths),
                "session_id": self.request.session_id,
            },
            "context": {
                "skill_directory": self.context.skill_directory,
                "shell_enabled": self.context.shell_enabled,
                "session_id": self.context.session_id,
                "substitution_values": dict(self.context.substitution_values),
            },
            "prompt_text": self.prompt_text,
            "shell_expansions": [item.to_payload() for item in self.shell_expansions],
            "pending_actions": [item.to_payload() for item in self.pending_actions],
        }


@dataclass(slots=True)
class SkillCandidateScoreBreakdown:
    path_score: int = 0
    agent_score: int = 0
    when_to_use_score: int = 0
    compatibility_score: int = 0
    allowed_tools_score: int = 0
    argument_readiness_score: int = 0
    effort_score: int = 0
    source_kind_score: int = 0
    matched_activation_paths: list[str] = field(default_factory=list)
    matched_agent_terms: list[str] = field(default_factory=list)
    matched_when_to_use_terms: list[str] = field(default_factory=list)
    matched_compatibility_terms: list[str] = field(default_factory=list)
    matched_allowed_tools: list[str] = field(default_factory=list)
    missing_allowed_tools: list[str] = field(default_factory=list)
    matched_argument_names: list[str] = field(default_factory=list)
    missing_argument_names: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def total_score(self) -> int:
        return (
            self.path_score
            + self.agent_score
            + self.when_to_use_score
            + self.compatibility_score
            + self.allowed_tools_score
            + self.argument_readiness_score
            + self.effort_score
            + self.source_kind_score
        )

    @property
    def total(self) -> int:
        return self.total_score

    def to_payload(self) -> dict[str, object]:
        return {
            "path_score": self.path_score,
            "agent_score": self.agent_score,
            "when_to_use_score": self.when_to_use_score,
            "compatibility_score": self.compatibility_score,
            "allowed_tools_score": self.allowed_tools_score,
            "argument_readiness_score": self.argument_readiness_score,
            "effort_score": self.effort_score,
            "source_kind_score": self.source_kind_score,
            "total_score": self.total_score,
            "matched_activation_paths": list(self.matched_activation_paths),
            "matched_agent_terms": list(self.matched_agent_terms),
            "matched_when_to_use_terms": list(self.matched_when_to_use_terms),
            "matched_compatibility_terms": list(self.matched_compatibility_terms),
            "matched_allowed_tools": list(self.matched_allowed_tools),
            "missing_allowed_tools": list(self.missing_allowed_tools),
            "matched_argument_names": list(self.matched_argument_names),
            "missing_argument_names": list(self.missing_argument_names),
            "penalties": list(self.penalties),
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class SkillResolutionRequest:
    touched_paths: list[str] = field(default_factory=list)
    user_goal: str | None = None
    current_prompt: str | None = None
    scenario_type: str | None = None
    agent_role: str | None = None
    workflow_stage: str | None = None
    workspace_path: str | None = None
    available_tools: list[str] = field(default_factory=list)
    invocation_arguments: dict[str, object] = field(default_factory=dict)
    top_k: int = 5
    include_reference_only: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "touched_paths": list(self.touched_paths),
            "user_goal": self.user_goal,
            "current_prompt": self.current_prompt,
            "scenario_type": self.scenario_type,
            "agent_role": self.agent_role,
            "workflow_stage": self.workflow_stage,
            "workspace_path": self.workspace_path,
            "available_tools": list(self.available_tools),
            "invocation_arguments": dict(self.invocation_arguments),
            "top_k": self.top_k,
            "include_reference_only": self.include_reference_only,
        }


@dataclass(slots=True)
class ResolvedSkillCandidate:
    compiled_skill: CompiledSkill
    score_breakdown: SkillCandidateScoreBreakdown
    rank: int = 0
    reasons: list[str] = field(default_factory=list)
    selected: bool = False
    role: SkillCandidateRole | None = None
    rejected_reason: str | None = None

    @property
    def total_score(self) -> int:
        return self.score_breakdown.total_score

    def to_payload(self, *, skill_payload: dict[str, object] | None = None) -> dict[str, object]:
        payload = dict(skill_payload or {})
        payload.update(
            {
                "total_score": self.total_score,
                "rank": self.rank,
                "score_breakdown": self.score_breakdown.to_payload(),
                "reasons": list(self.reasons),
                "selected": self.selected,
                "role": None if self.role is None else self.role.value,
                "rejected_reason": self.rejected_reason,
            }
        )
        return payload


@dataclass(slots=True)
class SkillResolutionResult:
    request: SkillResolutionRequest
    considered_candidates: list[ResolvedSkillCandidate] = field(default_factory=list)
    shortlisted_candidates: list[ResolvedSkillCandidate] = field(default_factory=list)
    primary_candidate: ResolvedSkillCandidate | None = None
    supporting_candidates: list[ResolvedSkillCandidate] = field(default_factory=list)
    reference_candidates: list[ResolvedSkillCandidate] = field(default_factory=list)
    rejected_candidates: list[ResolvedSkillCandidate] = field(default_factory=list)

    @property
    def selected_candidate(self) -> ResolvedSkillCandidate | None:
        return self.primary_candidate

    @property
    def all_selected_candidates(self) -> list[ResolvedSkillCandidate]:
        selected: list[ResolvedSkillCandidate] = []
        if self.primary_candidate is not None:
            selected.append(self.primary_candidate)
        selected.extend(self.supporting_candidates)
        return selected

    @property
    def active_candidate_count(self) -> int:
        return len(self.considered_candidates)

    @property
    def candidates(self) -> list[ResolvedSkillCandidate]:
        return self.considered_candidates

    @property
    def selected(self) -> list[ResolvedSkillCandidate]:
        return self.all_selected_candidates

    @property
    def rejected(self) -> list[ResolvedSkillCandidate]:
        return self.rejected_candidates

    def to_payload(
        self,
        *,
        payload_builder: Callable[[ResolvedSkillCandidate], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        def _serialize(candidate: ResolvedSkillCandidate) -> dict[str, object]:
            skill_payload = None if payload_builder is None else payload_builder(candidate)
            return candidate.to_payload(skill_payload=skill_payload)

        return {
            "request": self.request.to_payload(),
            "active_candidate_count": self.active_candidate_count,
            "selected_skill_id": (
                None
                if self.selected_candidate is None
                else self.selected_candidate.compiled_skill.skill_id
            ),
            "primary_candidate": (
                None if self.primary_candidate is None else _serialize(self.primary_candidate)
            ),
            "candidates": [_serialize(candidate) for candidate in self.considered_candidates],
            "shortlisted_candidates": [
                _serialize(candidate) for candidate in self.shortlisted_candidates
            ],
            "supporting_candidates": [
                _serialize(candidate) for candidate in self.supporting_candidates
            ],
            "all_selected_candidates": [
                _serialize(candidate) for candidate in self.all_selected_candidates
            ],
            "selected_skill_ids": [
                candidate.compiled_skill.skill_id for candidate in self.all_selected_candidates
            ],
            "selected": [_serialize(candidate) for candidate in self.all_selected_candidates],
            "reference_candidates": [
                _serialize(candidate) for candidate in self.reference_candidates
            ],
            "rejected_candidates": [
                _serialize(candidate) for candidate in self.rejected_candidates
            ],
            "rejected": [_serialize(candidate) for candidate in self.rejected_candidates],
        }


def skill_matches_touched_paths(compiled_skill: CompiledSkill, touched_paths: list[str]) -> bool:
    if not compiled_skill.activation_paths:
        return True
    normalized_paths = [_normalize_match_path(path) for path in touched_paths if path.strip()]
    if not normalized_paths:
        return False
    for pattern in compiled_skill.activation_paths:
        normalized_pattern = _normalize_match_path(pattern)
        for touched_path in normalized_paths:
            if _match_activation_pattern(normalized_pattern, touched_path):
                return True
    return False


def _normalize_match_path(path_value: str) -> str:
    return path_value.replace("\\", "/").strip().lstrip("./").casefold()


def _match_activation_pattern(pattern: str, touched_path: str) -> bool:
    if not pattern:
        return False
    if fnmatch.fnmatch(touched_path, pattern):
        return True
    if touched_path.endswith(f"/{pattern}"):
        return True
    if not any(character in pattern for character in "*?["):
        return touched_path == pattern or touched_path.startswith(f"{pattern}/")
    return False
