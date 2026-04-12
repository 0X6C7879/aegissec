from __future__ import annotations

import json
import math
import posixpath
from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import field_validator
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class SessionStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ERROR = "error"
    DONE = "done"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    STREAMING = "streaming"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageKind(str, Enum):
    MESSAGE = "message"
    SUMMARY = "summary"
    TRACE = "trace"
    EVENT_NOTE = "event_note"


class AssistantTranscriptSegmentKind(str, Enum):
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"
    ERROR = "error"
    STATUS = "status"


class GenerationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationAction(str, Enum):
    REPLY = "reply"
    EDIT = "edit"
    REGENERATE = "regenerate"
    FORK = "fork"
    ROLLBACK = "rollback"


class RuntimeContainerStatus(str, Enum):
    MISSING = "missing"
    STOPPED = "stopped"
    RUNNING = "running"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class RuntimeTerminalSessionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class RuntimeTerminalJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimePolicy(SQLModel):
    model_config = {"extra": "ignore"}

    allow_network: bool = True
    allow_write: bool = True
    max_execution_seconds: int = Field(default=300, gt=0)
    max_command_length: int = Field(default=4000, gt=0)


class CompatibilitySource(str, Enum):
    LOCAL = "local"
    CLAUDE = "claude"
    OPENCODE = "opencode"
    AGENTS = "agents"


class CompatibilityScope(str, Enum):
    PROJECT = "project"
    USER = "user"


class SkillRecordStatus(str, Enum):
    LOADED = "loaded"
    INVALID = "invalid"
    IGNORED = "ignored"


class MCPTransport(str, Enum):
    STDIO = "stdio"
    HTTP = "http"


class MCPServerStatus(str, Enum):
    INACTIVE = "inactive"
    CONNECTED = "connected"
    ERROR = "error"


class MCPCapabilityKind(str, Enum):
    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"
    RESOURCE_TEMPLATE = "resource_template"


class WorkflowRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"
    BLOCKED = "blocked"


WorkflowRunState = WorkflowRunStatus


class TaskNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskNodeType(str, Enum):
    STAGE = "stage"
    TASK = "task"


class GraphType(str, Enum):
    TASK = "task"
    EVIDENCE = "evidence"
    CAUSAL = "causal"
    ATTACK = "attack"


class ProjectBase(SQLModel):
    name: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class Project(ProjectBase, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True)


class ProjectSettings(SQLModel, table=True):
    __tablename__ = "project_settings"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True, unique=True)
    default_workflow_template: str | None = Field(default=None, max_length=120)
    default_runtime_profile_name: str | None = Field(default=None, max_length=120)
    default_queue_backend: str | None = Field(default=None, max_length=32)
    runtime_defaults_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("runtime_defaults", JSON, nullable=False),
    )
    notes: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class AttachmentMetadata(SQLModel):
    id: str | None = None
    name: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None


class AssistantTranscriptSegment(SQLModel):
    id: str
    sequence: int = Field(ge=1)
    kind: AssistantTranscriptSegmentKind
    status: str | None = None
    title: str | None = None
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    recorded_at: datetime
    updated_at: datetime
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")


class SessionBase(SQLModel):
    title: str = Field(default="New Session", max_length=200)
    status: SessionStatus = Field(default=SessionStatus.IDLE)
    project_id: str | None = Field(default=None, foreign_key="project.id")
    active_branch_id: str | None = Field(default=None)
    goal: str | None = Field(default=None, max_length=4000)
    scenario_type: str | None = Field(default=None, max_length=200)
    current_phase: str | None = Field(default=None, max_length=200)
    runtime_policy_json: dict[str, object] | None = Field(
        default=None,
        sa_column=Column("runtime_policy_json", JSON, nullable=True),
    )
    runtime_profile_name: str | None = Field(default=None, max_length=120)


class Session(SessionBase, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True)


class ConversationBranch(SQLModel, table=True):
    __tablename__ = "conversation_branch"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    parent_branch_id: str | None = Field(
        default=None, foreign_key="conversation_branch.id", nullable=True
    )
    forked_from_message_id: str | None = Field(default=None, nullable=True)
    name: str = Field(default="Main", max_length=200)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    parent_message_id: str | None = Field(default=None, foreign_key="message.id", nullable=True)
    branch_id: str | None = Field(default=None, foreign_key="conversation_branch.id", index=True)
    generation_id: str | None = Field(default=None, index=True)
    role: MessageRole
    status: MessageStatus = Field(default=MessageStatus.COMPLETED, index=True)
    message_kind: MessageKind = Field(default=MessageKind.MESSAGE, index=True)
    sequence: int = Field(default=0, ge=0, index=True)
    turn_index: int = Field(default=0, ge=0, index=True)
    edited_from_message_id: str | None = Field(
        default=None, foreign_key="message.id", nullable=True
    )
    version_group_id: str | None = Field(default=None, index=True)
    content: str
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    assistant_transcript_json: list[dict[str, object]] = Field(
        default_factory=list,
        sa_column=Column("assistant_transcript", JSON, nullable=False),
    )
    error_message: str | None = Field(default=None, nullable=True)
    attachments_json: list[dict[str, str | int | None]] = Field(
        default_factory=list,
        sa_column=Column("attachments", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    completed_at: datetime | None = Field(default=None, nullable=True)


class ChatGeneration(SQLModel, table=True):
    __tablename__ = "chat_generation"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    branch_id: str = Field(foreign_key="conversation_branch.id", index=True)
    action: GenerationAction = Field(default=GenerationAction.REPLY, nullable=False, index=True)
    user_message_id: str | None = Field(default=None, foreign_key="message.id", nullable=True)
    assistant_message_id: str = Field(foreign_key="message.id", index=True)
    target_message_id: str | None = Field(default=None, foreign_key="message.id", nullable=True)
    status: GenerationStatus = Field(default=GenerationStatus.QUEUED, nullable=False, index=True)
    reasoning_summary: str | None = Field(default=None, max_length=4000)
    reasoning_trace_json: list[dict[str, object]] = Field(
        default_factory=list,
        sa_column=Column("reasoning_trace", JSON, nullable=False),
    )
    error_message: str | None = Field(default=None, nullable=True)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False, index=True)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    started_at: datetime | None = Field(default=None, nullable=True)
    ended_at: datetime | None = Field(default=None, nullable=True)
    cancel_requested_at: datetime | None = Field(default=None, nullable=True)
    worker_id: str | None = Field(default=None, nullable=True, index=True)
    lease_claimed_at: datetime | None = Field(default=None, nullable=True)
    lease_expires_at: datetime | None = Field(default=None, nullable=True, index=True)
    attempt_count: int = Field(default=0, nullable=False, ge=0)


class GenerationStep(SQLModel, table=True):
    __tablename__ = "generation_step"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    generation_id: str = Field(foreign_key="chat_generation.id", index=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    message_id: str | None = Field(
        default=None, foreign_key="message.id", nullable=True, index=True
    )
    sequence: int = Field(nullable=False, ge=1, index=True)
    kind: str = Field(nullable=False, max_length=64, index=True)
    phase: str | None = Field(default=None, max_length=64, index=True)
    status: str = Field(nullable=False, max_length=32, index=True)
    state: str | None = Field(default=None, max_length=64, index=True)
    label: str | None = Field(default=None, max_length=200)
    safe_summary: str | None = Field(default=None, max_length=4000)
    delta_text: str = Field(default="", nullable=False)
    tool_name: str | None = Field(default=None, max_length=200, index=True)
    tool_call_id: str | None = Field(default=None, max_length=200, index=True)
    command: str | None = Field(default=None)
    started_at: datetime = Field(default_factory=utc_now, nullable=False, index=True)
    ended_at: datetime | None = Field(default=None, nullable=True)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )


class SessionEventLog(SQLModel, table=True):
    __tablename__ = "session_event_log"

    cursor: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    event_type: str = Field(nullable=False, index=True)
    timestamp: datetime = Field(default_factory=utc_now, nullable=False, index=True)
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON, nullable=False),
    )


class RuntimeExecutionRun(SQLModel, table=True):
    __tablename__ = "runtime_execution_run"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str | None = Field(default=None, foreign_key="session.id", index=True)
    command: str = Field(nullable=False)
    requested_timeout_seconds: int = Field(nullable=False, gt=0)
    status: ExecutionStatus = Field(nullable=False)
    exit_code: int | None = Field(default=None, nullable=True)
    stdout: str = Field(default="", nullable=False)
    stderr: str = Field(default="", nullable=False)
    container_name: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    ended_at: datetime = Field(default_factory=utc_now, nullable=False)


class RuntimeTerminalSession(SQLModel, table=True):
    __tablename__ = "runtime_terminal_sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    title: str = Field(default="Terminal", nullable=False, max_length=200)
    status: RuntimeTerminalSessionStatus = Field(
        default=RuntimeTerminalSessionStatus.OPEN,
        nullable=False,
        index=True,
    )
    shell: str = Field(default="/bin/zsh", nullable=False, max_length=200)
    cwd: str = Field(default="/workspace", nullable=False, max_length=1000)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False, index=True)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    closed_at: datetime | None = Field(default=None, nullable=True, index=True)


class RuntimeTerminalJob(SQLModel, table=True):
    __tablename__ = "runtime_terminal_jobs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    terminal_session_id: str = Field(foreign_key="runtime_terminal_sessions.id", index=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    status: RuntimeTerminalJobStatus = Field(
        default=RuntimeTerminalJobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    command: str = Field(nullable=False)
    exit_code: int | None = Field(default=None, nullable=True)
    started_at: datetime | None = Field(default=None, nullable=True, index=True)
    ended_at: datetime | None = Field(default=None, nullable=True)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False, index=True)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


class RuntimeArtifact(SQLModel, table=True):
    __tablename__ = "runtime_artifact"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(foreign_key="runtime_execution_run.id", index=True)
    relative_path: str = Field(nullable=False)
    host_path: str = Field(nullable=False)
    container_path: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class RunLog(SQLModel, table=True):
    __tablename__ = "run_log"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str | None = Field(default=None, foreign_key="session.id", index=True)
    project_id: str | None = Field(default=None, foreign_key="project.id", index=True)
    run_id: str | None = Field(default=None, foreign_key="runtime_execution_run.id", index=True)
    level: str = Field(default="info", nullable=False, index=True)
    source: str = Field(nullable=False, index=True)
    event_type: str = Field(nullable=False, index=True)
    message: str = Field(nullable=False)
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False, index=True)


class SkillRecord(SQLModel, table=True):
    __tablename__ = "skill_record"

    id: str = Field(primary_key=True)
    source: CompatibilitySource = Field(nullable=False, index=True)
    scope: CompatibilityScope = Field(nullable=False, index=True)
    root_dir: str = Field(nullable=False)
    directory_name: str = Field(nullable=False)
    entry_file: str = Field(nullable=False, index=True, unique=True)
    name: str = Field(nullable=False)
    description: str = Field(default="", nullable=False)
    compatibility_json: list[str] = Field(
        default_factory=list,
        sa_column=Column("compatibility", JSON, nullable=False),
    )
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    parameter_schema_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("parameter_schema", JSON, nullable=False),
    )
    raw_frontmatter_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("raw_frontmatter", JSON, nullable=False),
    )
    status: SkillRecordStatus = Field(nullable=False, index=True)
    enabled: bool = Field(default=True, nullable=False, index=True)
    error_message: str | None = Field(default=None, nullable=True)
    content_hash: str = Field(nullable=False)
    last_scanned_at: datetime = Field(default_factory=utc_now, nullable=False)


class MCPServer(SQLModel, table=True):
    __tablename__ = "mcp_server"

    id: str = Field(primary_key=True)
    name: str = Field(nullable=False, index=True)
    source: CompatibilitySource = Field(nullable=False, index=True)
    scope: CompatibilityScope = Field(nullable=False, index=True)
    transport: MCPTransport = Field(nullable=False, index=True)
    enabled: bool = Field(default=True, nullable=False)
    command: str | None = Field(default=None, nullable=True)
    args_json: list[str] = Field(
        default_factory=list, sa_column=Column("args", JSON, nullable=False)
    )
    env_json: dict[str, str] = Field(
        default_factory=dict, sa_column=Column("env", JSON, nullable=False)
    )
    url: str | None = Field(default=None, nullable=True)
    headers_json: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column("headers", JSON, nullable=False),
    )
    timeout_ms: int = Field(default=5000, nullable=False)
    status: MCPServerStatus = Field(default=MCPServerStatus.INACTIVE, nullable=False, index=True)
    last_error: str | None = Field(default=None, nullable=True)
    health_status: str | None = Field(default=None, nullable=True)
    health_latency_ms: int | None = Field(default=None, nullable=True)
    health_error: str | None = Field(default=None, nullable=True)
    health_checked_at: datetime | None = Field(default=None, nullable=True)
    config_path: str = Field(nullable=False)
    imported_at: datetime = Field(default_factory=utc_now, nullable=False)


class MCPCapability(SQLModel, table=True):
    __tablename__ = "mcp_capability"

    id: str = Field(primary_key=True)
    server_id: str = Field(foreign_key="mcp_server.id", index=True)
    kind: MCPCapabilityKind = Field(nullable=False, index=True)
    name: str = Field(nullable=False, index=True)
    title: str | None = Field(default=None, nullable=True)
    description: str | None = Field(default=None, nullable=True)
    uri: str | None = Field(default=None, nullable=True)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    input_schema_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("input_schema", JSON, nullable=False),
    )
    raw_payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("raw_payload", JSON, nullable=False),
    )


class WorkflowRun(SQLModel, table=True):
    __tablename__ = "workflow_run"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    template_name: str = Field(nullable=False, index=True)
    status: WorkflowRunStatus = Field(nullable=False, index=True)
    current_stage: str | None = Field(default=None, nullable=True)
    state_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("state", JSON, nullable=False),
    )
    last_error: str | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    started_at: datetime = Field(default_factory=utc_now, nullable=False)
    ended_at: datetime | None = Field(default=None, nullable=True)

    @property
    def state(self) -> WorkflowRunStatus:
        return self.status

    @state.setter
    def state(self, value: WorkflowRunStatus) -> None:
        self.status = value


class TaskNode(SQLModel, table=True):
    __tablename__ = "task_node"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workflow_run_id: str = Field(foreign_key="workflow_run.id", index=True)
    name: str = Field(nullable=False)
    node_type: TaskNodeType = Field(nullable=False, index=True)
    status: TaskNodeStatus = Field(nullable=False, index=True)
    sequence: int = Field(default=0, nullable=False, index=True, ge=0)
    parent_id: str | None = Field(default=None, foreign_key="task_node.id", nullable=True)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class GraphNode(SQLModel, table=True):
    __tablename__ = "graph_node"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    workflow_run_id: str = Field(foreign_key="workflow_run.id", index=True)
    graph_type: GraphType = Field(index=True)
    node_type: str = Field(nullable=False)
    label: str = Field(nullable=False)
    stable_key: str = Field(default="", nullable=False, index=True)
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class GraphEdge(SQLModel, table=True):
    __tablename__ = "graph_edge"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    workflow_run_id: str = Field(foreign_key="workflow_run.id", index=True)
    graph_type: GraphType = Field(index=True)
    source_node_id: str = Field(foreign_key="graph_node.id", index=True)
    target_node_id: str = Field(foreign_key="graph_node.id", index=True)
    relation: str = Field(nullable=False)
    stable_key: str = Field(default="", nullable=False, index=True)
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column("payload", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class GraphNodeWrite(SQLModel):
    id: str
    node_type: str
    label: str
    payload_json: dict[str, object] = Field(default_factory=dict)


class GraphEdgeWrite(SQLModel):
    id: str
    source_node_id: str
    target_node_id: str
    relation: str
    payload_json: dict[str, object] = Field(default_factory=dict)


class GraphSnapshot(SQLModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class SessionCreate(SQLModel):
    title: str | None = Field(default=None, max_length=200)
    project_id: str | None = None
    goal: str | None = Field(default=None, max_length=4000)
    scenario_type: str | None = Field(default=None, max_length=200)
    current_phase: str | None = Field(default=None, max_length=200)
    runtime_policy_json: dict[str, object] | None = None
    runtime_profile_name: str | None = Field(default=None, max_length=120)


class ProjectCreate(SQLModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class SessionUpdate(SQLModel):
    title: str | None = Field(default=None, max_length=200)
    status: SessionStatus | None = None
    project_id: str | None = None
    active_branch_id: str | None = None
    goal: str | None = Field(default=None, max_length=4000)
    scenario_type: str | None = Field(default=None, max_length=200)
    current_phase: str | None = Field(default=None, max_length=200)
    runtime_policy_json: dict[str, object] | None = None
    runtime_profile_name: str | None = Field(default=None, max_length=120)


class ProjectUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectSettingsRead(SQLModel):
    project_id: str
    default_workflow_template: str | None = None
    default_runtime_profile_name: str | None = None
    default_queue_backend: str | None = None
    runtime_defaults: dict[str, object] = Field(default_factory=dict)
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectSettingsUpdate(SQLModel):
    default_workflow_template: str | None = Field(default=None, max_length=120)
    default_runtime_profile_name: str | None = Field(default=None, max_length=120)
    default_queue_backend: str | None = Field(default=None, max_length=32)
    runtime_defaults: dict[str, object] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SessionRead(SessionBase):
    id: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ProjectRead(ProjectBase):
    id: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ProjectDetail(ProjectRead):
    sessions: list[SessionRead] = Field(default_factory=list)


class MessageRead(SQLModel):
    id: str
    session_id: str
    parent_message_id: str | None = None
    branch_id: str | None = None
    generation_id: str | None = None
    role: MessageRole
    status: MessageStatus
    message_kind: MessageKind
    sequence: int
    turn_index: int
    edited_from_message_id: str | None = None
    version_group_id: str | None = None
    content: str
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    assistant_transcript: list[AssistantTranscriptSegment] = Field(default_factory=list)
    error_message: str | None = None
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None


class ConversationBranchRead(SQLModel):
    id: str
    session_id: str
    parent_branch_id: str | None = None
    forked_from_message_id: str | None = None
    name: str
    created_at: datetime
    updated_at: datetime


class ChatGenerationRead(SQLModel):
    id: str
    session_id: str
    branch_id: str
    action: GenerationAction
    user_message_id: str | None = None
    assistant_message_id: str
    target_message_id: str | None = None
    status: GenerationStatus
    reasoning_summary: str | None = None
    reasoning_trace: list[dict[str, object]] = Field(default_factory=list)
    steps: list[GenerationStepRead] = Field(default_factory=list)
    error_message: str | None = None
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    queue_position: int | None = None


class SessionDetail(SessionRead):
    messages: list[MessageRead] = Field(default_factory=list)


class GenerationStepRead(SQLModel):
    id: str
    generation_id: str
    session_id: str
    message_id: str | None = None
    sequence: int
    kind: str
    phase: str | None = None
    status: str
    state: str | None = None
    label: str | None = None
    safe_summary: str | None = None
    delta_text: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None
    command: str | None = None
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    started_at: datetime
    ended_at: datetime | None = None


class SessionConversationRead(SQLModel):
    session: SessionRead
    active_branch: ConversationBranchRead | None = None
    branches: list[ConversationBranchRead] = Field(default_factory=list)
    messages: list[MessageRead] = Field(default_factory=list)
    generations: list[ChatGenerationRead] = Field(default_factory=list)
    active_generation_id: str | None = None
    queued_generation_count: int = 0


class SessionQueueRead(SQLModel):
    session: SessionRead
    active_generation: ChatGenerationRead | None = None
    queued_generations: list[ChatGenerationRead] = Field(default_factory=list)
    active_generation_id: str | None = None
    queued_generation_count: int = 0


class SessionReplayRead(SQLModel):
    session: SessionRead
    branches: list[ConversationBranchRead] = Field(default_factory=list)
    messages: list[MessageRead] = Field(default_factory=list)
    generations: list[ChatGenerationRead] = Field(default_factory=list)


class SessionContextWindowBreakdownRead(SQLModel):
    key: str
    label: str
    estimated_tokens: int
    share_ratio: float


class SessionContextWindowRead(SQLModel):
    session_id: str
    model: str
    context_window_tokens: int
    used_tokens: int
    reserved_response_tokens: int
    usage_ratio: float
    auto_compact_threshold_ratio: float
    last_compacted_at: datetime | None = None
    last_compact_boundary: str | None = None
    can_manual_compact: bool
    blocking_reason: str | None = None
    breakdown: list[SessionContextWindowBreakdownRead] = Field(default_factory=list)


class SessionCompactRequest(SQLModel):
    mode: Literal["manual"] = Field(default="manual")


class SessionCompactResponse(SQLModel):
    session_id: str
    mode: str
    compacted: bool
    compact_boundary: str | None = None
    before_tokens: int
    after_tokens: int
    reclaimed_tokens: int
    summary: str
    created_at: datetime


class MessageRegenerateRequest(SQLModel):
    branch_id: str | None = None
    token_budget: int | None = Field(default=None, gt=0)


class MessageRollbackRequest(SQLModel):
    branch_id: str | None = None


class GenerationCancelRequest(SQLModel):
    reason: str | None = Field(default=None, max_length=2000)


class SlashActionInvocation(SQLModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, object] = Field(default_factory=dict)
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None


class SlashActionSelection(SQLModel):
    id: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    display_text: str = Field(min_length=1)
    invocation: SlashActionInvocation


class SlashCatalogItem(SQLModel):
    id: str
    trigger: str
    title: str
    description: str
    type: str
    source: str
    badge: str
    keybind: str | None = None
    disabled: bool | None = None
    action: SlashActionSelection


class ChatRequest(SQLModel):
    content: str = Field(min_length=1)
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    branch_id: str | None = None
    parent_message_id: str | None = None
    token_budget: int | None = Field(default=None, gt=0)
    wait_for_completion: bool = False
    slash_action: SlashActionSelection | None = None


class ChatResponse(SQLModel):
    session: SessionRead
    user_message: MessageRead
    assistant_message: MessageRead
    generation: ChatGenerationRead | None = None
    branch: ConversationBranchRead | None = None
    queue_position: int | None = None
    active_generation_id: str | None = None
    queued_generation_count: int = 0


class MessageEditRequest(SQLModel):
    content: str = Field(min_length=1)
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    branch_id: str | None = None
    token_budget: int | None = Field(default=None, gt=0)


class BranchForkRequest(SQLModel):
    name: str | None = Field(default=None, max_length=200)


class MessageMutationResponse(SQLModel):
    session: SessionRead
    branch: ConversationBranchRead
    user_message: MessageRead | None = None
    assistant_message: MessageRead | None = None
    generation: ChatGenerationRead | None = None


class RuntimeArtifactRead(SQLModel):
    id: str
    run_id: str
    relative_path: str
    host_path: str
    container_path: str
    created_at: datetime


class RunLogRead(SQLModel):
    id: str
    session_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    level: str
    source: str
    event_type: str
    message: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class RuntimeExecutionRunRead(SQLModel):
    id: str
    session_id: str | None = None
    command: str
    requested_timeout_seconds: int
    status: ExecutionStatus
    exit_code: int | None = None
    stdout: str
    stderr: str
    container_name: str
    created_at: datetime
    started_at: datetime
    ended_at: datetime
    artifacts: list[RuntimeArtifactRead] = Field(default_factory=list)


class RuntimeContainerStateRead(SQLModel):
    status: RuntimeContainerStatus
    container_name: str
    image: str
    container_id: str | None = None
    workspace_host_path: str
    workspace_container_path: str
    started_at: datetime | None = None


class RuntimeStatusRead(SQLModel):
    runtime: RuntimeContainerStateRead
    recent_runs: list[RuntimeExecutionRunRead] = Field(default_factory=list)
    recent_artifacts: list[RuntimeArtifactRead] = Field(default_factory=list)


class RuntimeExecuteRequest(SQLModel):
    command: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, gt=0)
    session_id: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)


class RuntimeProfileRead(SQLModel):
    name: str
    policy: RuntimePolicy


TERMINAL_METADATA_MAX_BYTES = 4_096
TERMINAL_METADATA_MAX_DEPTH = 4
TERMINAL_METADATA_MAX_ITEMS = 32
TERMINAL_ALLOWED_SHELLS = frozenset({"/bin/zsh", "/bin/bash", "/bin/sh"})
TERMINAL_ALLOWED_CWD_PREFIX = "/workspace"


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _normalize_terminal_metadata_value(value: object, *, depth: int = 0) -> object:
    if depth > TERMINAL_METADATA_MAX_DEPTH:
        raise ValueError(
            f"Terminal metadata exceeds max nesting depth of {TERMINAL_METADATA_MAX_DEPTH}."
        )

    if value is None or isinstance(value, bool | int | str):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Terminal metadata must not contain NaN or Infinity.")
        return value

    if isinstance(value, list):
        if len(value) > TERMINAL_METADATA_MAX_ITEMS:
            raise ValueError(
                f"Terminal metadata lists may not exceed {TERMINAL_METADATA_MAX_ITEMS} items."
            )
        return [_normalize_terminal_metadata_value(item, depth=depth + 1) for item in value]

    if isinstance(value, dict):
        if len(value) > TERMINAL_METADATA_MAX_ITEMS:
            raise ValueError(
                f"Terminal metadata objects may not exceed {TERMINAL_METADATA_MAX_ITEMS} keys."
            )

        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Terminal metadata keys must be strings.")
            normalized[key] = _normalize_terminal_metadata_value(item, depth=depth + 1)
        return normalized

    raise ValueError(
        "Terminal metadata values must be JSON-serializable primitives, lists, or objects."
    )


class TerminalSessionCreateRequest(SQLModel):
    title: str | None = Field(default=None, max_length=200)
    shell: str = Field(default="/bin/zsh", min_length=1, max_length=200)
    cwd: str = Field(default="/workspace", min_length=1, max_length=1000)
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized and _contains_control_characters(normalized):
            raise ValueError("must not contain control characters")
        return normalized or None

    @field_validator("shell")
    @classmethod
    def _validate_shell(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        if _contains_control_characters(normalized):
            raise ValueError("must not contain control characters")
        if any(character.isspace() for character in normalized):
            raise ValueError("must not contain whitespace")
        if normalized not in TERMINAL_ALLOWED_SHELLS:
            raise ValueError(f"must be one of: {', '.join(sorted(TERMINAL_ALLOWED_SHELLS))}")
        return normalized

    @field_validator("cwd")
    @classmethod
    def _validate_cwd(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        if _contains_control_characters(normalized):
            raise ValueError("must not contain control characters")
        normalized_path = posixpath.normpath(normalized)
        if not normalized_path.startswith("/"):
            raise ValueError("must be an absolute POSIX path")
        if normalized_path != TERMINAL_ALLOWED_CWD_PREFIX and not normalized_path.startswith(
            f"{TERMINAL_ALLOWED_CWD_PREFIX}/"
        ):
            raise ValueError(f"must stay within {TERMINAL_ALLOWED_CWD_PREFIX}")
        if "/../" in f"{normalized_path}/" or normalized_path.endswith("/.."):
            raise ValueError("must not contain traversal segments")
        return normalized_path

    @field_validator("metadata_payload")
    @classmethod
    def _validate_metadata_payload(cls, value: dict[str, object]) -> dict[str, object]:
        normalized = _normalize_terminal_metadata_value(value)
        if not isinstance(normalized, dict):
            raise ValueError("Terminal metadata must be an object.")

        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > TERMINAL_METADATA_MAX_BYTES:
            raise ValueError(
                f"Terminal metadata exceeds max size of {TERMINAL_METADATA_MAX_BYTES} bytes."
            )

        return normalized


class TerminalSessionRead(SQLModel):
    id: str
    session_id: str
    title: str
    status: RuntimeTerminalSessionStatus
    shell: str
    cwd: str
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class TerminalJobRead(SQLModel):
    id: str
    terminal_session_id: str
    session_id: str
    status: RuntimeTerminalJobStatus
    command: str
    exit_code: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    created_at: datetime
    updated_at: datetime


class TerminalInputRequest(SQLModel):
    data: str = Field(min_length=1, max_length=16_384)


class TerminalResizeRequest(SQLModel):
    cols: int = Field(ge=1, le=1_000)
    rows: int = Field(ge=1, le=1_000)


class TerminalExecuteRequest(SQLModel):
    command: str = Field(min_length=1, max_length=4_000)
    detach: bool = False
    timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)
    artifact_paths: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        if _contains_control_characters(normalized):
            raise ValueError("must not contain control characters")
        return normalized

    @field_validator("artifact_paths")
    @classmethod
    def _validate_artifact_paths(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = item.strip()
            if not cleaned:
                raise ValueError("artifact paths must not be blank")
            if _contains_control_characters(cleaned):
                raise ValueError("artifact paths must not contain control characters")
            if cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized


class TerminalExecuteResponse(SQLModel):
    terminal_id: str
    accepted: bool = True
    detach: bool
    job_id: str | None = None
    status: str


class TerminalJobTailRead(SQLModel):
    job_id: str
    session_id: str
    terminal_session_id: str
    status: RuntimeTerminalJobStatus
    stream: Literal["stdout", "stderr"]
    lines: int = Field(ge=1)
    tail: str = ""
    ended_at: datetime | None = None
    updated_at: datetime


class TerminalJobsCleanupRequest(SQLModel):
    limit: int | None = Field(default=None, ge=1, le=1_000)


class TerminalJobsCleanupResult(SQLModel):
    deleted_jobs: int = Field(ge=0)
    kept_jobs: int = Field(ge=0)


class SkillRecordRead(SQLModel):
    id: str
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str
    directory_name: str
    entry_file: str
    name: str
    description: str
    compatibility: list[str] = Field(default_factory=list)
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    parameter_schema: dict[str, object] = Field(default_factory=dict)
    raw_frontmatter: dict[str, object] = Field(default_factory=dict)
    status: SkillRecordStatus
    enabled: bool
    error_message: str | None = None
    content_hash: str
    last_scanned_at: datetime
    source_kind: str | None = None
    loaded_from: str | None = None
    invocable: bool = True
    conditional: bool = False
    active: bool = False
    dynamic: bool = False
    when_to_use: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    context: str | None = None
    agent: str | None = None
    effort: str | None = None
    version: str | None = None
    model_hint: str | None = None
    verification_mode: str | None = None
    shell_profile: str | None = None
    trust_level: str | None = None
    preflight_checks: list[dict[str, object]] = Field(default_factory=list)
    orchestration_role: str | None = None
    orchestration_hints: dict[str, object] | None = None
    fanout_group: str | None = None
    preferred_stage: str | None = None
    context_strategy: str | None = None
    execution_policy: dict[str, object] | None = None
    result_schema: dict[str, object] | None = None
    aliases: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    shell_enabled: bool = True
    prepared_invocation: dict[str, object] | None = None
    resolved_identity: dict[str, object] = Field(default_factory=dict)
    discovery_provenance: dict[str, object] = Field(default_factory=dict)


class SkillAgentSummaryRead(SQLModel):
    id: str
    name: str
    directory_name: str
    description: str
    compatibility: list[str] = Field(default_factory=list)
    entry_file: str
    source: CompatibilitySource | None = None
    scope: CompatibilityScope | None = None
    source_kind: str | None = None
    loaded_from: str | None = None
    invocable: bool = True
    user_invocable: bool | None = None
    conditional: bool = False
    active: bool = False
    dynamic: bool = False
    paths: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    when_to_use: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    context: str | None = None
    agent: str | None = None
    effort: str | None = None
    version: str | None = None
    model_hint: str | None = None
    verification_mode: str | None = None
    shell_profile: str | None = None
    trust_level: str | None = None
    preflight_checks: list[dict[str, object]] = Field(default_factory=list)
    orchestration_role: str | None = None
    orchestration_hints: dict[str, object] | None = None
    fanout_group: str | None = None
    preferred_stage: str | None = None
    context_strategy: str | None = None
    execution_policy: dict[str, object] | None = None
    result_schema: dict[str, object] | None = None
    family: str | None = None
    domain: str | None = None
    task_mode: str | None = None
    tags: list[str] = Field(default_factory=list)
    argument_hint: str | None = None
    shell_enabled: bool = True
    execution_mode: str | None = None
    resolved_identity: dict[str, object] = Field(default_factory=dict)
    prepared_invocation: dict[str, object] | None = None
    prepared_for_context: bool = False
    prepared_for_execution: bool = False
    active_due_to_touched_paths: bool = False
    discovery_provenance: dict[str, object] = Field(default_factory=dict)
    rank: int = 0
    total_score: int = 0
    score_breakdown: dict[str, object] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    selected: bool = False
    role: str | None = None
    rejected_reason: str | None = None


class SkillContentRead(SQLModel):
    id: str
    name: str
    directory_name: str
    entry_file: str
    parameter_schema: dict[str, object] = Field(default_factory=dict)
    source: CompatibilitySource | None = None
    scope: CompatibilityScope | None = None
    source_kind: str | None = None
    loaded_from: str | None = None
    invocable: bool = True
    conditional: bool = False
    active: bool = False
    dynamic: bool = False
    when_to_use: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    context: str | None = None
    agent: str | None = None
    effort: str | None = None
    version: str | None = None
    model_hint: str | None = None
    verification_mode: str | None = None
    shell_profile: str | None = None
    trust_level: str | None = None
    preflight_checks: list[dict[str, object]] = Field(default_factory=list)
    orchestration_role: str | None = None
    orchestration_hints: dict[str, object] | None = None
    fanout_group: str | None = None
    preferred_stage: str | None = None
    context_strategy: str | None = None
    execution_policy: dict[str, object] | None = None
    result_schema: dict[str, object] | None = None
    aliases: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    shell_enabled: bool = True
    prepared_invocation: dict[str, object] | None = None
    resolved_identity: dict[str, object] = Field(default_factory=dict)
    discovery_provenance: dict[str, object] = Field(default_factory=dict)
    content: str


class MCPCapabilityRead(SQLModel):
    kind: MCPCapabilityKind
    name: str
    title: str | None = None
    description: str | None = None
    uri: str | None = None
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    input_schema: dict[str, object] = Field(default_factory=dict)
    raw_payload: dict[str, object] = Field(default_factory=dict)


class MCPServerRead(SQLModel):
    id: str
    name: str
    source: CompatibilitySource
    scope: CompatibilityScope
    transport: MCPTransport
    enabled: bool
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_ms: int
    status: MCPServerStatus
    last_error: str | None = None
    health_status: str | None = None
    health_latency_ms: int | None = None
    health_error: str | None = None
    health_checked_at: datetime | None = None
    config_path: str
    imported_at: datetime
    capabilities: list[MCPCapabilityRead] = Field(default_factory=list)


class TaskNodeRead(SQLModel):
    id: str
    workflow_run_id: str
    name: str
    node_type: TaskNodeType
    status: TaskNodeStatus
    sequence: int
    parent_id: str | None = None
    metadata_payload: dict[str, object] = Field(default_factory=dict, alias="metadata")
    created_at: datetime


class WorkflowRunRead(SQLModel):
    id: str
    session_id: str
    template_name: str
    status: WorkflowRunStatus
    current_stage: str | None = None
    state_payload: dict[str, object] = Field(default_factory=dict, alias="state")
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime
    ended_at: datetime | None = None


class WorkflowRunDetailRead(WorkflowRunRead):
    tasks: list[TaskNodeRead] = Field(default_factory=list)


class WorkflowTemplateStageRead(SQLModel):
    key: str
    title: str
    role: str
    phase: str
    role_prompt: str = ""
    sub_agent_role_prompt: str = ""
    requires_approval: bool = False


class WorkflowTemplateRead(SQLModel):
    name: str
    title: str
    description: str
    template_kinds: list[str] = Field(default_factory=list)
    stages: list[WorkflowTemplateStageRead] = Field(default_factory=list)


class WorkflowRunReplayStepRead(SQLModel):
    index: int
    trace_id: str
    task_node_id: str
    task_name: str
    status: str
    started_at: str
    ended_at: str
    summary: str | None = None
    evidence_confidence: float | None = None
    retry_attempt: int | None = None
    batch_cycle: int | None = None


class WorkflowRunReplayRead(SQLModel):
    run_id: str
    session_id: str
    template_name: str
    status: WorkflowRunStatus
    current_stage: str | None = None
    replay_steps: list[WorkflowRunReplayStepRead] = Field(default_factory=list)
    replan_records: list[dict[str, object]] = Field(default_factory=list)
    batch_state: dict[str, object] = Field(default_factory=dict)


class SessionGraphNodeRead(SQLModel):
    id: str
    graph_type: GraphType
    node_type: str
    label: str
    data: dict[str, object] = Field(default_factory=dict)


class SessionGraphEdgeRead(SQLModel):
    id: str
    graph_type: GraphType
    source: str
    target: str
    relation: str
    data: dict[str, object] = Field(default_factory=dict)


class SessionGraphRead(SQLModel):
    session_id: str
    workflow_run_id: str
    graph_type: GraphType
    current_stage: str | None = None
    nodes: list[SessionGraphNodeRead] = Field(default_factory=list)
    edges: list[SessionGraphEdgeRead] = Field(default_factory=list)


class WorkflowRunExportRead(SQLModel):
    run: WorkflowRunDetailRead
    task_graph: SessionGraphRead
    evidence_graph: SessionGraphRead
    causal_graph: SessionGraphRead
    attack_graph: SessionGraphRead
    execution_records: list[dict[str, object]] = Field(default_factory=list)
    replan_records: list[dict[str, object]] = Field(default_factory=list)
    batch_state: dict[str, object] = Field(default_factory=dict)


def attachments_to_storage(
    attachments: list[AttachmentMetadata],
) -> list[dict[str, str | int | None]]:
    return [attachment.model_dump(mode="python") for attachment in attachments]


def attachments_from_storage(
    attachments: list[dict[str, str | int | None]],
) -> list[AttachmentMetadata]:
    return [AttachmentMetadata.model_validate(attachment) for attachment in attachments]


def assistant_transcript_to_storage(
    segments: list[AssistantTranscriptSegment],
) -> list[dict[str, object]]:
    return [segment.model_dump(mode="json", by_alias=True) for segment in segments]


def assistant_transcript_from_storage(
    segments: list[dict[str, object]],
) -> list[AssistantTranscriptSegment]:
    parsed_segments: list[AssistantTranscriptSegment] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        parsed_segments.append(AssistantTranscriptSegment.model_validate(segment))
    parsed_segments.sort(key=lambda item: (item.sequence, item.recorded_at, item.id))
    return parsed_segments


def _infer_legacy_transcript_kind(entry: dict[str, object]) -> AssistantTranscriptSegmentKind:
    state = str(entry.get("state") or "")
    if state == "summary.updated":
        return AssistantTranscriptSegmentKind.REASONING
    if state == "tool.started":
        return AssistantTranscriptSegmentKind.TOOL_CALL
    if state == "tool.finished":
        return AssistantTranscriptSegmentKind.TOOL_RESULT
    if state in {"tool.failed", "generation.failed"}:
        return AssistantTranscriptSegmentKind.ERROR
    return AssistantTranscriptSegmentKind.STATUS


def _infer_legacy_transcript_status(entry: dict[str, object]) -> str | None:
    status = entry.get("status")
    if isinstance(status, str) and status:
        return status

    state = str(entry.get("state") or "")
    if state in {"generation.started", "tool.started"}:
        return "running"
    if state in {"generation.completed", "tool.finished", "summary.updated"}:
        return "completed"
    if state in {"generation.failed", "tool.failed"}:
        return "failed"
    if state == "generation.cancelled":
        return "cancelled"
    return None


def _legacy_transcript_text(entry: dict[str, object]) -> str | None:
    for key in ("text", "summary", "error"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def build_legacy_assistant_transcript(message: Message) -> list[AssistantTranscriptSegment]:
    if message.role != MessageRole.ASSISTANT or message.message_kind != MessageKind.MESSAGE:
        return []

    metadata = dict(message.metadata_json)
    raw_trace = metadata.get("trace")
    trace_entries = (
        [entry for entry in raw_trace if isinstance(entry, dict)]
        if isinstance(raw_trace, list)
        else []
    )
    segments: list[AssistantTranscriptSegment] = []
    next_sequence = 1

    if not trace_entries:
        summary = metadata.get("summary")
        if isinstance(summary, str) and summary:
            timestamp = message.created_at
            segments.append(
                AssistantTranscriptSegment(
                    id=f"legacy-{message.id}-reasoning-1",
                    sequence=next_sequence,
                    kind=AssistantTranscriptSegmentKind.REASONING,
                    status="completed",
                    title=None,
                    text=summary,
                    recorded_at=timestamp,
                    updated_at=timestamp,
                    metadata={},
                )
            )
            next_sequence += 1

    for index, entry in enumerate(trace_entries, start=1):
        raw_sequence = entry.get("sequence")
        sequence = (
            raw_sequence if isinstance(raw_sequence, int) and raw_sequence > 0 else next_sequence
        )
        next_sequence = max(next_sequence, sequence + 1)
        raw_recorded_at = entry.get("recorded_at")
        recorded_at = (
            raw_recorded_at if isinstance(raw_recorded_at, str) else message.created_at.isoformat()
        )
        parsed_recorded_at = datetime.fromisoformat(recorded_at)
        metadata_payload = dict(entry)
        metadata_payload.pop("sequence", None)
        metadata_payload.pop("recorded_at", None)
        segments.append(
            AssistantTranscriptSegment(
                id=f"legacy-{message.id}-{sequence}-{index}",
                sequence=sequence,
                kind=_infer_legacy_transcript_kind(entry),
                status=_infer_legacy_transcript_status(entry),
                title=str(entry.get("event")) if isinstance(entry.get("event"), str) else None,
                text=_legacy_transcript_text(entry),
                tool_name=str(entry.get("tool")) if isinstance(entry.get("tool"), str) else None,
                tool_call_id=(
                    str(entry.get("tool_call_id"))
                    if isinstance(entry.get("tool_call_id"), str)
                    else None
                ),
                recorded_at=parsed_recorded_at,
                updated_at=parsed_recorded_at,
                metadata=metadata_payload,
            )
        )

    if message.content.strip():
        status_value = (
            message.status.value.lower()
            if isinstance(message.status.value, str)
            else str(message.status)
        )
        segments.append(
            AssistantTranscriptSegment(
                id=f"legacy-{message.id}-output-{next_sequence}",
                sequence=next_sequence,
                kind=AssistantTranscriptSegmentKind.OUTPUT,
                status=status_value,
                title=None,
                text=message.content,
                recorded_at=message.completed_at or message.created_at,
                updated_at=message.completed_at or message.created_at,
                metadata={},
            )
        )

    segments.sort(key=lambda item: (item.sequence, item.recorded_at, item.id))
    return segments


def resolve_message_assistant_transcript(message: Message) -> list[AssistantTranscriptSegment]:
    if message.assistant_transcript_json:
        return assistant_transcript_from_storage(message.assistant_transcript_json)
    return build_legacy_assistant_transcript(message)


def to_message_read(message: Message) -> MessageRead:
    return MessageRead(
        id=message.id,
        session_id=message.session_id,
        parent_message_id=message.parent_message_id,
        branch_id=message.branch_id,
        generation_id=message.generation_id,
        role=message.role,
        status=message.status,
        message_kind=message.message_kind,
        sequence=message.sequence,
        turn_index=message.turn_index,
        edited_from_message_id=message.edited_from_message_id,
        version_group_id=message.version_group_id,
        content=message.content,
        **{"metadata": dict(message.metadata_json)},
        assistant_transcript=resolve_message_assistant_transcript(message),
        error_message=message.error_message,
        attachments=attachments_from_storage(message.attachments_json),
        created_at=message.created_at,
        completed_at=message.completed_at,
    )


def to_conversation_branch_read(branch: ConversationBranch) -> ConversationBranchRead:
    return ConversationBranchRead(
        id=branch.id,
        session_id=branch.session_id,
        parent_branch_id=branch.parent_branch_id,
        forked_from_message_id=branch.forked_from_message_id,
        name=branch.name,
        created_at=branch.created_at,
        updated_at=branch.updated_at,
    )


def to_chat_generation_read(generation: ChatGeneration) -> ChatGenerationRead:
    return ChatGenerationRead(
        id=generation.id,
        session_id=generation.session_id,
        branch_id=generation.branch_id,
        action=generation.action,
        user_message_id=generation.user_message_id,
        assistant_message_id=generation.assistant_message_id,
        target_message_id=generation.target_message_id,
        status=generation.status,
        reasoning_summary=generation.reasoning_summary,
        reasoning_trace=list(generation.reasoning_trace_json),
        steps=[],
        error_message=generation.error_message,
        **{"metadata": dict(generation.metadata_json)},
        created_at=generation.created_at,
        updated_at=generation.updated_at,
        started_at=generation.started_at,
        ended_at=generation.ended_at,
        cancel_requested_at=generation.cancel_requested_at,
        queue_position=None,
    )


def to_generation_step_read(step: GenerationStep) -> GenerationStepRead:
    return GenerationStepRead(
        id=step.id,
        generation_id=step.generation_id,
        session_id=step.session_id,
        message_id=step.message_id,
        sequence=step.sequence,
        kind=step.kind,
        phase=step.phase,
        status=step.status,
        state=step.state,
        label=step.label,
        safe_summary=step.safe_summary,
        delta_text=step.delta_text,
        tool_name=step.tool_name,
        tool_call_id=step.tool_call_id,
        command=step.command,
        **{"metadata": dict(step.metadata_json)},
        started_at=step.started_at,
        ended_at=step.ended_at,
    )


def to_session_read(session: Session) -> SessionRead:
    return SessionRead(
        id=session.id,
        title=session.title,
        status=session.status,
        project_id=session.project_id,
        active_branch_id=session.active_branch_id,
        goal=session.goal,
        scenario_type=session.scenario_type,
        current_phase=session.current_phase,
        runtime_policy_json=(
            dict(session.runtime_policy_json) if session.runtime_policy_json is not None else None
        ),
        runtime_profile_name=session.runtime_profile_name,
        created_at=session.created_at,
        updated_at=session.updated_at,
        deleted_at=session.deleted_at,
    )


def to_session_detail(session: Session, messages: list[Message]) -> SessionDetail:
    return SessionDetail(
        **to_session_read(session).model_dump(),
        messages=[to_message_read(message) for message in messages],
    )


def to_project_read(project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        deleted_at=project.deleted_at,
    )


def to_project_detail(project: Project, sessions: list[Session]) -> ProjectDetail:
    return ProjectDetail(
        **to_project_read(project).model_dump(),
        sessions=[to_session_read(session) for session in sessions],
    )


def to_project_settings_read(project_settings: ProjectSettings) -> ProjectSettingsRead:
    return ProjectSettingsRead(
        project_id=project_settings.project_id,
        default_workflow_template=project_settings.default_workflow_template,
        default_runtime_profile_name=project_settings.default_runtime_profile_name,
        default_queue_backend=project_settings.default_queue_backend,
        runtime_defaults=dict(project_settings.runtime_defaults_json),
        notes=project_settings.notes,
        created_at=project_settings.created_at,
        updated_at=project_settings.updated_at,
    )


def to_runtime_artifact_read(artifact: RuntimeArtifact) -> RuntimeArtifactRead:
    return RuntimeArtifactRead(
        id=artifact.id,
        run_id=artifact.run_id,
        relative_path=artifact.relative_path,
        host_path=artifact.host_path,
        container_path=artifact.container_path,
        created_at=artifact.created_at,
    )


def to_run_log_read(run_log: RunLog) -> RunLogRead:
    return RunLogRead(
        id=run_log.id,
        session_id=run_log.session_id,
        project_id=run_log.project_id,
        run_id=run_log.run_id,
        level=run_log.level,
        source=run_log.source,
        event_type=run_log.event_type,
        message=run_log.message,
        payload=dict(run_log.payload_json),
        created_at=run_log.created_at,
    )


def to_runtime_execution_run_read(
    run: RuntimeExecutionRun,
    artifacts: list[RuntimeArtifact],
) -> RuntimeExecutionRunRead:
    return RuntimeExecutionRunRead(
        id=run.id,
        session_id=run.session_id,
        command=run.command,
        requested_timeout_seconds=run.requested_timeout_seconds,
        status=run.status,
        exit_code=run.exit_code,
        stdout=run.stdout,
        stderr=run.stderr,
        container_name=run.container_name,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        artifacts=[to_runtime_artifact_read(artifact) for artifact in artifacts],
    )


def to_terminal_session_read(terminal_session: RuntimeTerminalSession) -> TerminalSessionRead:
    return TerminalSessionRead.model_validate(
        {
            "id": terminal_session.id,
            "session_id": terminal_session.session_id,
            "title": terminal_session.title,
            "status": terminal_session.status,
            "shell": terminal_session.shell,
            "cwd": terminal_session.cwd,
            "metadata": dict(terminal_session.metadata_json),
            "created_at": terminal_session.created_at,
            "updated_at": terminal_session.updated_at,
            "closed_at": terminal_session.closed_at,
        }
    )


def to_terminal_job_read(terminal_job: RuntimeTerminalJob) -> TerminalJobRead:
    return TerminalJobRead.model_validate(
        {
            "id": terminal_job.id,
            "terminal_session_id": terminal_job.terminal_session_id,
            "session_id": terminal_job.session_id,
            "status": terminal_job.status,
            "command": terminal_job.command,
            "exit_code": terminal_job.exit_code,
            "started_at": terminal_job.started_at,
            "ended_at": terminal_job.ended_at,
            "metadata": dict(terminal_job.metadata_json),
            "created_at": terminal_job.created_at,
            "updated_at": terminal_job.updated_at,
        }
    )


def to_skill_record_read(record: SkillRecord) -> SkillRecordRead:
    return SkillRecordRead(
        metadata_payload=dict(record.metadata_json),  # pyright: ignore[reportCallIssue]
        id=record.id,
        source=record.source,
        scope=record.scope,
        root_dir=record.root_dir,
        directory_name=record.directory_name,
        entry_file=record.entry_file,
        name=record.name,
        description=record.description,
        compatibility=list(record.compatibility_json),
        parameter_schema=dict(record.parameter_schema_json),
        raw_frontmatter={
            key: value for key, value in record.raw_frontmatter_json.items() if key != "_compat"
        },
        status=record.status,
        enabled=record.enabled,
        error_message=record.error_message,
        content_hash=record.content_hash,
        last_scanned_at=record.last_scanned_at,
    )


def to_mcp_capability_read(capability: MCPCapability) -> MCPCapabilityRead:
    return MCPCapabilityRead(
        **{"metadata": dict(capability.metadata_json)},
        kind=capability.kind,
        name=capability.name,
        title=capability.title,
        description=capability.description,
        uri=capability.uri,
        input_schema=dict(capability.input_schema_json),
        raw_payload=dict(capability.raw_payload_json),
    )


def to_mcp_server_read(
    server: MCPServer,
    capabilities: list[MCPCapabilityRead],
) -> MCPServerRead:
    return MCPServerRead(
        id=server.id,
        name=server.name,
        source=server.source,
        scope=server.scope,
        transport=server.transport,
        enabled=server.enabled,
        command=server.command,
        args=list(server.args_json),
        env=dict(server.env_json),
        url=server.url,
        headers=dict(server.headers_json),
        timeout_ms=server.timeout_ms,
        status=server.status,
        last_error=server.last_error,
        health_status=server.health_status,
        health_latency_ms=server.health_latency_ms,
        health_error=server.health_error,
        health_checked_at=server.health_checked_at,
        config_path=server.config_path,
        imported_at=server.imported_at,
        capabilities=capabilities,
    )


def to_task_node_read(task_node: TaskNode) -> TaskNodeRead:
    return TaskNodeRead(
        **{"metadata": dict(task_node.metadata_json)},
        id=task_node.id,
        workflow_run_id=task_node.workflow_run_id,
        name=task_node.name,
        node_type=task_node.node_type,
        status=task_node.status,
        sequence=task_node.sequence,
        parent_id=task_node.parent_id,
        created_at=task_node.created_at,
    )


def to_workflow_run_read(run: WorkflowRun) -> WorkflowRunRead:
    return WorkflowRunRead(
        **{"state": dict(run.state_json)},
        id=run.id,
        session_id=run.session_id,
        template_name=run.template_name,
        status=run.status,
        current_stage=run.current_stage,
        last_error=run.last_error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def to_workflow_run_detail_read(run: WorkflowRun, tasks: list[TaskNode]) -> WorkflowRunDetailRead:
    return WorkflowRunDetailRead(
        **{"state": dict(run.state_json)},
        id=run.id,
        session_id=run.session_id,
        template_name=run.template_name,
        status=run.status,
        current_stage=run.current_stage,
        last_error=run.last_error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        tasks=[to_task_node_read(task_node) for task_node in tasks],
    )
