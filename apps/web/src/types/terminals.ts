export type TerminalSessionStatus =
  | "open"
  | "closed"
  | "idle"
  | "busy"
  | "error"
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
  shell: string;
  cwd: string;
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
  terminal_id?: string | null;
  stream: "stdout" | "stderr";
  content: string;
  lines: number;
  status: TerminalJobStatus;
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
