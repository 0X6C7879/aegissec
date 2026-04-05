from __future__ import annotations

import fnmatch
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
