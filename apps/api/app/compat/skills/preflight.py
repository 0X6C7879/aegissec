from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

AUTO_RUN_TRUST_LEVELS = frozenset({"bundled_trusted", "local_trusted"})
AUTO_RUN_KINDS = frozenset(
    {
        "cwd",
        "directory",
        "file_preview",
        "git_diff_stat",
        "git_log",
        "git_status",
        "list_dir",
        "ls",
        "pwd",
        "repo_state",
        "workspace",
    }
)


@dataclass(slots=True)
class SkillPreflightCheck:
    name: str
    kind: str = "generic"
    required: bool = True
    read_only: bool = True
    description: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "read_only": self.read_only,
        }
        if self.description is not None:
            payload["description"] = self.description
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


class SkillPreflightResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(slots=True)
class SkillPreflightResult:
    name: str
    kind: str
    status: SkillPreflightResultStatus
    required: bool = True
    read_only: bool = True
    auto_ran: bool = False
    output_summary: str | None = None
    warning: str | None = None
    error: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "kind": self.kind,
            "status": self.status.value,
            "required": self.required,
            "read_only": self.read_only,
            "auto_ran": self.auto_ran,
        }
        if self.output_summary is not None:
            payload["output_summary"] = self.output_summary
        if self.warning is not None:
            payload["warning"] = self.warning
        if self.error is not None:
            payload["error"] = self.error
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def normalize_preflight_kind(check: SkillPreflightCheck) -> str:
    value = (check.kind or check.name).strip().casefold()
    return value.replace("-", "_").replace(" ", "_")


def can_auto_run_preflight(check: SkillPreflightCheck, *, trust_level: str | None) -> bool:
    return (
        check.read_only
        and trust_level in AUTO_RUN_TRUST_LEVELS
        and normalize_preflight_kind(check) in AUTO_RUN_KINDS
    )
