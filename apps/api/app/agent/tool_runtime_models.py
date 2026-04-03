from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from app.db.models import TaskNodeStatus

if TYPE_CHECKING:
    from app.agent.tool_registry import ToolExecutionRequest, ToolPolicyDecision, ToolSpec


class ToolInterruptBehavior(str, Enum):
    NONE = "none"
    REQUIRE_APPROVAL = "require_approval"
    USER_INTERACTION = "user_interaction"


class ToolValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = dict(details or {})


class ToolExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.details = dict(details or {})


@dataclass(frozen=True)
class ToolTranscriptBlock:
    kind: str
    content: str
    metadata: dict[str, object] = field(default_factory=dict)
    is_metadata_only: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "content": self.content,
            "metadata": dict(self.metadata),
            "is_metadata_only": self.is_metadata_only,
        }


@dataclass(frozen=True)
class ToolRuntimeResult:
    spec: ToolSpec
    source_type: str
    source_name: str
    command_or_action: str
    input_payload: dict[str, object]
    output_payload: dict[str, object]
    status: TaskNodeStatus
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True)
class ToolExecutionEnvelope:
    request: ToolExecutionRequest
    spec: ToolSpec
    runtime_result: ToolRuntimeResult
    transcript_blocks: tuple[ToolTranscriptBlock, ...] = ()
    policy_decision: ToolPolicyDecision | None = None
    runtime_protocol: dict[str, object] = field(default_factory=dict)
