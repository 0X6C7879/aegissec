export type RuntimeStatus = "missing" | "stopped" | "running";

export type RuntimeRunStatus = "success" | "failed" | "timeout" | (string & {});

export type RuntimeHealthStatus = "ok" | "degraded" | (string & {});

export type RuntimePolicy = {
  allow_network: boolean;
  allow_write: boolean;
  max_execution_seconds: number;
  max_command_length: number;
};

export type RuntimeArtifact = {
  id: string;
  run_id: string;
  relative_path: string;
  host_path: string;
  container_path: string;
  created_at: string;
};

export type RuntimeState = {
  status: RuntimeStatus;
  container_name: string;
  image: string;
  container_id: string | null;
  workspace_host_path: string;
  workspace_container_path: string;
  started_at: string | null;
};

export type RuntimeHealth = {
  status: RuntimeHealthStatus;
  runtime_status: RuntimeStatus;
  container_name: string;
  image: string;
  container_id: string | null;
  workspace_host_path: string;
  workspace_container_path: string;
  started_at: string | null;
};

export type RuntimeExecutionRun = {
  id: string;
  session_id: string | null;
  command: string;
  requested_timeout_seconds: number;
  status: RuntimeRunStatus;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  container_name: string;
  created_at: string;
  started_at: string;
  ended_at: string;
  artifacts: RuntimeArtifact[];
};

export type RuntimeStatusResponse = {
  runtime: RuntimeState;
  recent_runs: RuntimeExecutionRun[];
  recent_artifacts: RuntimeArtifact[];
};

export type RuntimeProfile = {
  name: string;
  policy: RuntimePolicy;
};

export type RuntimeArtifactsCleanupResult = {
  deleted_files: number;
  deleted_rows: number;
  kept: number;
};

export type RuntimeRunsClearResult = {
  deleted_runs: number;
  deleted_artifacts: number;
};

export type RuntimeExecuteRequest = {
  command: string;
  timeout_seconds?: number | null;
  session_id?: string | null;
  artifact_paths: string[];
};
