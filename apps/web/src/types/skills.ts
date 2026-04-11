export type SkillCompatibilitySource = "local" | "claude" | "opencode" | "agents" | (string & {});

export type SkillCompatibilityScope = "project" | "user" | (string & {});

export type SkillRecordStatus = "loaded" | "invalid" | "ignored" | (string & {});

export type SkillParameterSchema = Record<string, unknown>;

export type SkillRecord = {
  id: string;
  source: SkillCompatibilitySource;
  scope: SkillCompatibilityScope;
  root_dir: string;
  directory_name: string;
  entry_file: string;
  name: string;
  description: string;
  compatibility: string[];
  metadata: Record<string, unknown>;
  parameter_schema: SkillParameterSchema;
  raw_frontmatter: Record<string, unknown>;
  status: SkillRecordStatus;
  enabled: boolean;
  error_message: string | null;
  content_hash: string;
  last_scanned_at: string;
};

export type SkillContent = {
  id: string;
  name: string;
  directory_name: string;
  entry_file: string;
  parameter_schema: SkillParameterSchema;
  content: string;
};

export type SkillContextSkill = {
  id: string;
  name: string;
  directory_name: string;
  description: string;
  compatibility: string[];
  parameter_schema: SkillParameterSchema;
};

export type SkillOrchestrationRole =
  | "primary"
  | "supporting"
  | "reference"
  | "worker"
  | "reducer"
  | "verifier"
  | (string & {});

export type SkillOrchestrationSkill = {
  id?: string;
  name?: string;
  directory_name?: string;
  role?: SkillOrchestrationRole | null;
  prepared_for_context?: boolean;
  prepared_for_execution?: boolean;
  status?: string | null;
  [key: string]: unknown;
};

export type SkillOrchestrationStep = {
  step_id?: string;
  name?: string;
  role?: SkillOrchestrationRole | null;
  execution_intent?: string | null;
  stage_name?: string | null;
  skill_id?: string | null;
  directory_name?: string | null;
  node_kind?: string | null;
  status?: string | null;
  duration_ms?: number;
  summary_for_prompt?: string | null;
  failure_reason?: string | null;
  warnings?: string[];
  [key: string]: unknown;
};

export type SkillOrchestrationStage = {
  stage_name?: string | null;
  mode?: string | null;
  failure_policy?: string | null;
  max_parallel_workers?: number;
  steps?: SkillOrchestrationStep[];
  [key: string]: unknown;
};

export type SkillOrchestrationPlan = {
  active_stage?: string | null;
  stages?: SkillOrchestrationStage[];
  selected_skill_ids?: string[];
  primary_skill_id?: string | null;
  [key: string]: unknown;
};

export type SkillStageTransition = {
  from_stage?: string | null;
  to_stage?: string | null;
  replan_required?: boolean;
  triggered_by?: string[];
  reasons?: string[];
  [key: string]: unknown;
};

export type SkillOrchestrationExecution = {
  active_stage?: string | null;
  mode?: string | null;
  failure_policy?: string | null;
  status?: string | null;
  duration_ms?: number;
  worker_results?: SkillOrchestrationStep[];
  node_results?: SkillOrchestrationStep[];
  reduction_result?: Record<string, unknown> | null;
  verification_result?: Record<string, unknown> | null;
  stage_transition?: SkillStageTransition | null;
  [key: string]: unknown;
};

export type SkillContext = {
  payload: {
    skills: SkillContextSkill[];
    selected_skills?: SkillOrchestrationSkill[];
    prepared_selected_skills?: SkillOrchestrationSkill[];
    skill_orchestration_plan?: SkillOrchestrationPlan;
    skill_orchestration_execution?: SkillOrchestrationExecution;
    skill_stage_transition?: SkillStageTransition;
    replanned_skill_context?: Record<string, unknown>;
    [key: string]: unknown;
  };
  prompt_fragment: string;
};
