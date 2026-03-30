export type RuntimeStatus = "missing" | "stopped" | "running";

export type RuntimeRunStatus = "success" | "failed" | "timeout";

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

export type RuntimeExecuteRequest = {
  command: string;
  timeout_seconds?: number;
  session_id?: string | null;
  artifact_paths: string[];
};
