export type TerminalSessionStatus =
  | "open"
  | "closed"
  | (string & {});

export type TerminalWorkbenchStatus =
  | "idle"
  | "attached"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | (string & {});

export type TerminalJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timeout"
  | (string & {});

export type TerminalSession = {
  id: string;
  session_id: string;
  title: string;
  status: TerminalSessionStatus;
  workbench_status: TerminalWorkbenchStatus;
  shell: string;
  cwd: string;
  attached: boolean;
  active_job_id?: string | null;
  last_job_id?: string | null;
  last_job_status?: TerminalJobStatus | null;
  reattach_deadline?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  closed_at?: string | null;
};

export type TerminalJob = {
  id: string;
  terminal_session_id: string;
  session_id: string;
  status: TerminalJobStatus;
  command: string;
  exit_code?: number | null;
  started_at?: string | null;
  ended_at?: string | null;
  finish_reason?: string | null;
  stdout_tail: string;
  stderr_tail: string;
  run_id?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type TerminalExecuteResponse = {
  terminal_id: string;
  job_id: string | null;
  status: TerminalJobStatus | TerminalSessionStatus;
};

export type TerminalJobTail = {
  job_id: string;
  session_id: string;
  terminal_session_id: string;
  stream: "stdout" | "stderr";
  tail: string;
  lines: number;
  status: TerminalJobStatus;
  ended_at?: string | null;
  updated_at: string;
};

export type TerminalBuffer = {
  session_id: string;
  terminal_id: string;
  attached: boolean;
  job_id?: string | null;
  reattach_deadline?: string | null;
  lines: number;
  buffer: string;
};

export type TerminalJobsCleanupResult = {
  deleted_jobs: number;
  kept_jobs: number;
};

export type TerminalStreamFrame =
  | {
      type: "ready";
      session_id: string;
      terminal_id: string;
      job_id: string;
      reattached: boolean;
    }
  | {
      type: "output";
      data: string;
    }
  | {
      type: "error";
      message: string;
    }
  | {
      type: "exit";
      exit_code: number | null;
      reason: string;
    }
  | {
      type: "closed";
      reason: string;
    };

export type TerminalClientFrame =
  | { type: "input"; data: string }
  | { type: "resize"; cols: number; rows: number }
  | { type: "signal"; signal: string }
  | { type: "interrupt" }
  | { type: "eof" }
  | { type: "close" };
