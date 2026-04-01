from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

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


class RuntimeContainerStatus(str, Enum):
    MISSING = "missing"
    STOPPED = "stopped"
    RUNNING = "running"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


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


class SessionBase(SQLModel):
    title: str = Field(default="New Session", max_length=200)
    status: SessionStatus = Field(default=SessionStatus.IDLE)
    project_id: str | None = Field(default=None, foreign_key="project.id")
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


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    role: MessageRole
    content: str
    attachments_json: list[dict[str, str | int | None]] = Field(
        default_factory=list,
        sa_column=Column("attachments", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


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
    role: MessageRole
    content: str
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    created_at: datetime


class SessionDetail(SessionRead):
    messages: list[MessageRead] = Field(default_factory=list)


class ChatRequest(SQLModel):
    content: str = Field(min_length=1)
    attachments: list[AttachmentMetadata] = Field(default_factory=list)


class ChatResponse(SQLModel):
    session: SessionRead
    user_message: MessageRead
    assistant_message: MessageRead


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


class SkillAgentSummaryRead(SQLModel):
    id: str
    name: str
    directory_name: str
    description: str
    compatibility: list[str] = Field(default_factory=list)
    entry_file: str


class SkillContentRead(SQLModel):
    id: str
    name: str
    directory_name: str
    entry_file: str
    parameter_schema: dict[str, object] = Field(default_factory=dict)
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


def to_message_read(message: Message) -> MessageRead:
    return MessageRead(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        attachments=attachments_from_storage(message.attachments_json),
        created_at=message.created_at,
    )


def to_session_read(session: Session) -> SessionRead:
    return SessionRead(
        id=session.id,
        title=session.title,
        status=session.status,
        project_id=session.project_id,
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


def to_skill_record_read(record: SkillRecord) -> SkillRecordRead:
    return SkillRecordRead(
        **{"metadata": dict(record.metadata_json)},
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
        raw_frontmatter=dict(record.raw_frontmatter_json),
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
