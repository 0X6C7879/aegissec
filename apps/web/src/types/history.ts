export type SessionHistoryEntry = {
  id: string;
  session_id: string | null;
  project_id: string | null;
  run_id: string | null;
  level: string;
  source: string;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};
