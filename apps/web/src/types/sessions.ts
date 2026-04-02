export type SessionStatus =
  | "idle"
  | "running"
  | "paused"
  | "cancelled"
  | "error"
  | "done"
  | (string & {});

export type MessageRole = "user" | "assistant" | "system" | "tool" | (string & {});

export type MessageStatus =
  | "pending"
  | "queued"
  | "streaming"
  | "completed"
  | "failed"
  | "cancelled"
  | "superseded"
  | (string & {});

export type MessageKind = "message" | "summary" | "trace" | "event_note" | (string & {});

export type GenerationStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | (string & {});

export type GenerationStepKind =
  | "reasoning"
  | "tool"
  | "output"
  | "status"
  | (string & {});

export type GenerationStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | (string & {});

export type GenerationStepPhase =
  | "planning"
  | "tool_selection"
  | "tool_running"
  | "tool_result"
  | "synthesis"
  | "completed"
  | "failed"
  | "cancelled"
  | (string & {});

export type GenerationAction =
  | "reply"
  | "edit"
  | "regenerate"
  | "fork"
  | "rollback"
  | (string & {});

export type AttachmentMetadata = {
  id: string;
  name: string;
  content_type: string;
  size_bytes: number;
};

export type RuntimePolicy = Record<string, unknown>;

export type GenerationReasoningTraceEntry = Record<string, unknown> & {
  type?: string;
  event?: string;
  kind?: string;
  cursor?: number | null;
  sequence?: number | null;
  recorded_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  timestamp?: string | null;
  emitted_at?: string | null;
  payload?: Record<string, unknown>;
  data?: Record<string, unknown>;
  summary?: string | null;
  safe_summary?: string | null;
  status_text?: string | null;
  message?: string | null;
  label?: string | null;
  title?: string | null;
  status?: string | null;
  phase?: string | null;
  error?: string | null;
  message_id?: string | null;
  assistant_message_id?: string | null;
  generation_id?: string | null;
};

export type SessionSummary = {
  id: string;
  title: string;
  status: SessionStatus;
  project_id: string | null;
  active_branch_id?: string | null;
  goal: string | null;
  scenario_type: string | null;
  current_phase: string | null;
  runtime_policy_json: RuntimePolicy | null;
  runtime_profile_name?: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};

export type ConversationBranch = {
  id: string;
  session_id: string;
  parent_branch_id?: string | null;
  forked_from_message_id?: string | null;
  name: string;
  created_at: string;
  updated_at: string;
};

export type ChatGeneration = {
  id: string;
  session_id: string;
  branch_id: string;
  action: GenerationAction;
  user_message_id?: string | null;
  assistant_message_id: string;
  target_message_id?: string | null;
  status: GenerationStatus;
  reasoning_summary?: string | null;
  reasoning_trace?: GenerationReasoningTraceEntry[];
  steps?: GenerationStep[];
  metadata?: Record<string, unknown>;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  cancel_requested_at?: string | null;
  queue_position?: number | null;
};

export type GenerationStep = {
  id: string;
  generation_id: string;
  session_id: string;
  message_id?: string | null;
  sequence: number;
  kind: GenerationStepKind;
  phase?: GenerationStepPhase | null;
  status: GenerationStepStatus;
  state?: string | null;
  label?: string | null;
  safe_summary?: string | null;
  delta_text: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
  command?: string | null;
  metadata?: Record<string, unknown>;
  started_at: string;
  ended_at?: string | null;
};

export type SessionMessage = {
  id: string;
  session_id: string;
  parent_message_id?: string | null;
  branch_id?: string | null;
  generation_id?: string | null;
  role: MessageRole;
  status?: MessageStatus;
  message_kind?: MessageKind;
  sequence?: number;
  turn_index?: number;
  edited_from_message_id?: string | null;
  version_group_id?: string | null;
  content: string;
  metadata?: Record<string, unknown>;
  error_message?: string | null;
  attachments: AttachmentMetadata[];
  created_at: string;
  completed_at?: string | null;
};

export type SessionDetail = SessionSummary & {
  messages: SessionMessage[];
};

export type SessionConversation = {
  session: SessionSummary;
  active_branch: ConversationBranch | null;
  branches: ConversationBranch[];
  messages: SessionMessage[];
  generations: ChatGeneration[];
  active_generation_id?: string | null;
  queued_generation_count?: number;
};

export type SessionQueue = {
  session: SessionSummary;
  active_generation: ChatGeneration | null;
  queued_generations: ChatGeneration[];
  active_generation_id?: string | null;
  queued_generation_count?: number;
};

export type SessionReplay = {
  session: SessionSummary;
  branches: ConversationBranch[];
  messages: SessionMessage[];
  generations: ChatGeneration[];
};

export type ChatResponse = {
  session: SessionSummary;
  user_message: SessionMessage;
  assistant_message: SessionMessage;
  generation?: ChatGeneration | null;
  branch?: ConversationBranch | null;
  queue_position?: number | null;
  active_generation_id?: string | null;
  queued_generation_count?: number;
};

export type SessionEventEnvelope = {
  type: string;
  cursor?: number | null;
  created_at?: string;
  data?: unknown;
};

export type SessionEventEntry = {
  id: string;
  sessionId: string;
  cursor?: number | null;
  type: string;
  createdAt: string;
  summary: string;
  payload?: unknown;
};
