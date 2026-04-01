import type { SessionSummary } from "./sessions";

export type ProjectSummary = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};

export type ProjectDetail = ProjectSummary & {
  sessions: SessionSummary[];
};

export type ProjectCreateRequest = {
  name: string;
  description?: string | null;
};

export type ProjectUpdateRequest = {
  name?: string | null;
  description?: string | null;
};
