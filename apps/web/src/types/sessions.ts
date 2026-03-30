export type SessionStatus =
  | "idle"
  | "running"
  | "paused"
  | "error"
  | "done"
  | (string & {});

export type MessageRole = "user" | "assistant" | "system" | (string & {});

export type AttachmentMetadata = {
  id: string;
  name: string;
  content_type: string;
  size_bytes: number;
};

export type SessionSummary = {
  id: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};

export type SessionMessage = {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  attachments: AttachmentMetadata[];
  created_at: string;
};

export type SessionDetail = SessionSummary & {
  messages: SessionMessage[];
};

export type ChatResponse = {
  session: SessionSummary;
  user_message: SessionMessage;
  assistant_message: SessionMessage;
};

export type SessionEventEnvelope = {
  type: string;
  created_at?: string;
  data?: unknown;
};

export type SessionEventEntry = {
  id: string;
  sessionId: string;
  type: string;
  createdAt: string;
  summary: string;
  payload?: unknown;
};
