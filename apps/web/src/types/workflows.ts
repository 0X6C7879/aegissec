import type { SessionGraph } from "./graphs";

export type WorkflowRunStatus =
  | "queued"
  | "running"
  | "needs_approval"
  | "paused"
  | "done"
  | "error"
  | "blocked"
  | (string & {});

export type TaskNodeStatus =
  | "pending"
  | "ready"
  | "in_progress"
  | "blocked"
  | "completed"
  | "failed"
  | "skipped"
  | (string & {});

export type WorkflowTaskMetadata = {
  planner_key?: string;
  title?: string;
  description?: string;
  summary?: string;
  role?: string;
  stage_key?: string;
  depends_on_planner_keys?: string[];
  depends_on_task_ids?: string[];
  priority?: number;
  sibling_priority_rank?: number;
  approval_required?: boolean;
  execution_state?: string;
  attempt_count?: number;
  retry_count?: number;
  retry_limit?: number;
  retry_scheduled?: boolean;
  evidence_confidence?: number | null;
  last_attempt_status?: string;
  role_prompt?: string;
  sub_agent_role_prompt?: string;
  kind?: string;
} & Record<string, unknown>;

export type WorkflowTaskNode = {
  id: string;
  workflow_run_id: string;
  name: string;
  node_type: "stage" | "task" | (string & {});
  status: TaskNodeStatus;
  sequence: number;
  parent_id: string | null;
  metadata: WorkflowTaskMetadata;
  created_at: string;
};

export type WorkflowBatchState = {
  contract_version?: string;
  cycle?: number;
  status?: string;
  max_nodes_per_cycle?: number;
  selected_task_ids?: string[];
  executed_task_ids?: string[];
  started_at?: string | null;
  ended_at?: string | null;
} & Record<string, unknown>;

export type WorkflowCompactionBucket = {
  trim_count?: number;
  archived_count?: number;
  last_trimmed_at?: string | null;
} & Record<string, unknown>;

export type WorkflowCompactionState = {
  execution?: WorkflowCompactionBucket;
  messages?: WorkflowCompactionBucket;
} & Record<string, unknown>;

export type WorkflowApprovalState = {
  required?: boolean;
  pending_task_id?: string | null;
} & Record<string, unknown>;

export type WorkflowPlanNode = {
  planner_key?: string;
  name?: string;
  node_type?: string;
  title?: string;
  description?: string;
  stage_key?: string;
  role?: string;
  sequence?: number;
  depends_on?: string[];
  parent_key?: string | null;
  priority?: number;
  approval_required?: boolean;
  role_prompt?: string;
  sub_agent_role_prompt?: string;
} & Record<string, unknown>;

export type WorkflowPlanState = {
  summary?: string;
  stage_order?: string[];
  nodes?: WorkflowPlanNode[];
} & Record<string, unknown>;

export type WorkflowExecutionRecord = {
  id?: string;
  session_id?: string;
  task_node_id?: string;
  source_type?: string;
  source_name?: string;
  command_or_action?: string;
  input_json?: Record<string, unknown>;
  output_json?: Record<string, unknown>;
  status?: string;
  batch_cycle?: number | null;
  retry_attempt?: number | null;
  retry_count?: number | null;
  summary?: string | null;
  evidence_confidence?: number | null;
  started_at?: string;
  ended_at?: string;
} & Record<string, unknown>;

export type WorkflowReplanRecord = {
  id?: string;
  trace_id?: string;
  task_node_id?: string;
  task_name?: string;
  reason?: string | null;
  suggestion?: string | null;
  recorded_at?: string;
} & Record<string, unknown>;

export type WorkflowRunStateData = {
  session_id?: string;
  goal?: string;
  template?: string;
  plan?: WorkflowPlanState;
  current_stage?: string | null;
  stage_order?: string[];
  messages?: Array<Record<string, unknown>>;
  archived_messages?: Array<Record<string, unknown>>;
  skill_snapshot?: Array<Record<string, unknown>>;
  mcp_snapshot?: Array<Record<string, unknown>>;
  runtime_policy?: Record<string, unknown>;
  seed_message_id?: string | null;
  findings?: Array<Record<string, unknown>>;
  graph_updates?: Array<Record<string, unknown>>;
  execution_records?: WorkflowExecutionRecord[];
  archived_execution_records?: WorkflowExecutionRecord[];
  hypothesis_updates?: Array<Record<string, unknown>>;
  compaction?: WorkflowCompactionState;
  approval?: WorkflowApprovalState;
  replan_records?: WorkflowReplanRecord[];
  batch?: WorkflowBatchState;
} & Record<string, unknown>;

export type WorkflowRunDetail = {
  id: string;
  session_id: string;
  template_name: string;
  status: WorkflowRunStatus;
  current_stage: string | null;
  state: WorkflowRunStateData;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string;
  ended_at: string | null;
  tasks: WorkflowTaskNode[];
};

export type WorkflowTemplateStage = {
  key: string;
  title: string;
  role: string;
  phase: string;
  role_prompt: string;
  sub_agent_role_prompt: string;
  requires_approval: boolean;
};

export type WorkflowTemplate = {
  name: string;
  title: string;
  description: string;
  template_kinds: string[];
  stages: WorkflowTemplateStage[];
};

export type WorkflowRunReplayStep = {
  index: number;
  trace_id: string;
  task_node_id: string;
  task_name: string;
  status: string;
  started_at: string;
  ended_at: string;
  summary: string | null;
  evidence_confidence: number | null;
  retry_attempt: number | null;
  batch_cycle: number | null;
};

export type WorkflowRunReplay = {
  run_id: string;
  session_id: string;
  template_name: string;
  status: WorkflowRunStatus;
  current_stage: string | null;
  replay_steps: WorkflowRunReplayStep[];
  replan_records: WorkflowReplanRecord[];
  batch_state: WorkflowBatchState;
};

export type WorkflowRunExport = {
  run: WorkflowRunDetail;
  task_graph: SessionGraph;
  evidence_graph: SessionGraph;
  causal_graph: SessionGraph;
  execution_records: WorkflowExecutionRecord[];
  replan_records: WorkflowReplanRecord[];
  batch_state: WorkflowBatchState;
};

export type WorkflowStartRequest = {
  session_id: string;
  template_name?: string | null;
  seed_message_id?: string | null;
};

export type WorkflowTaskPriorityReorderRequest = {
  ordered_task_ids: string[];
};
