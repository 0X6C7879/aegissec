import type {
  AttachmentMetadata,
  ChatResponse,
  SessionDetail,
  SessionSummary,
} from "../types/sessions";
import type { SessionGraph } from "../types/graphs";
import type {
  RuntimeExecuteRequest,
  RuntimeExecutionRun,
  RuntimeState,
  RuntimeStatusResponse,
} from "../types/runtime";
import type { MCPServer } from "../types/mcp";
import type { ModelApiSettings, ModelApiSettingsUpdate } from "../types/settings";
import type { SkillRecord } from "../types/skills";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

type ChatRequestPayload = {
  content: string;
  attachments?: AttachmentMetadata[];
};

async function readErrorMessage(response: Response): Promise<string> {
  const fallbackMessage = `Request failed (HTTP ${response.status})`;
  const clonedResponse = response.clone();
  const payload = await clonedResponse.json().catch((): unknown => null);

  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string") {
      return detail;
    }
  }

  try {
    const text = await response.text();
    return text || fallbackMessage;
  } catch {
    return fallbackMessage;
  }
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function getApiBaseUrl(): string {
  return apiBaseUrl;
}

export async function listSessions(
  includeDeleted: boolean,
  signal?: AbortSignal,
): Promise<SessionSummary[]> {
  const searchParams = new URLSearchParams();

  if (includeDeleted) {
    searchParams.set("include_deleted", "true");
  }

  const query = searchParams.size > 0 ? `?${searchParams.toString()}` : "";
  return apiRequest<SessionSummary[]>(`/api/sessions${query}`, { signal });
}

export async function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionDetail> {
  return apiRequest<SessionDetail>(`/api/sessions/${sessionId}`, { signal });
}

export async function getTaskGraph(sessionId: string, signal?: AbortSignal): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/task`, { signal });
}

export async function getCausalGraph(sessionId: string, signal?: AbortSignal): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/causal`, { signal });
}

export async function createSession(title?: string): Promise<SessionSummary> {
  return apiRequest<SessionSummary>("/api/sessions", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });
}

export async function updateSession(
  sessionId: string,
  payload: Partial<Pick<SessionSummary, "title" | "status">>,
): Promise<SessionSummary> {
  return apiRequest<SessionSummary>(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteSession(sessionId: string): Promise<void> {
  return apiRequest<void>(`/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export async function restoreSession(sessionId: string): Promise<SessionSummary> {
  return apiRequest<SessionSummary>(`/api/sessions/${sessionId}/restore`, {
    method: "POST",
  });
}

export async function sendChatMessage(
  sessionId: string,
  payload: ChatRequestPayload,
): Promise<ChatResponse> {
  return apiRequest<ChatResponse>(`/api/sessions/${sessionId}/chat`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSessionEventsUrl(sessionId: string): string {
  return `${apiBaseUrl.replace(/^http/, "ws")}/api/sessions/${sessionId}/events`;
}

export async function listSkills(signal?: AbortSignal): Promise<SkillRecord[]> {
  return apiRequest<SkillRecord[]>("/api/skills", { signal });
}

export async function getSkill(skillId: string, signal?: AbortSignal): Promise<SkillRecord> {
  return apiRequest<SkillRecord>(`/api/skills/${skillId}`, { signal });
}

export async function rescanSkills(): Promise<SkillRecord[]> {
  return apiRequest<SkillRecord[]>("/api/skills/rescan", {
    method: "POST",
  });
}

export async function getRuntimeStatus(signal?: AbortSignal): Promise<RuntimeStatusResponse> {
  return apiRequest<RuntimeStatusResponse>("/api/runtime/status", { signal });
}

export async function startRuntime(): Promise<RuntimeState> {
  return apiRequest<RuntimeState>("/api/runtime/start", {
    method: "POST",
  });
}

export async function stopRuntime(): Promise<RuntimeState> {
  return apiRequest<RuntimeState>("/api/runtime/stop", {
    method: "POST",
  });
}

export async function executeRuntimeCommand(
  payload: RuntimeExecuteRequest,
): Promise<RuntimeExecutionRun> {
  return apiRequest<RuntimeExecutionRun>("/api/runtime/execute", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function importMcpServers(): Promise<MCPServer[]> {
  return apiRequest<MCPServer[]>("/api/mcp/import", {
    method: "POST",
  });
}

export async function listMcpServers(signal?: AbortSignal): Promise<MCPServer[]> {
  return apiRequest<MCPServer[]>("/api/mcp/servers", { signal });
}

export async function getMcpServer(serverId: string, signal?: AbortSignal): Promise<MCPServer> {
  return apiRequest<MCPServer>(`/api/mcp/servers/${serverId}`, { signal });
}

export async function toggleMcpServer(serverId: string, enabled: boolean): Promise<MCPServer> {
  return apiRequest<MCPServer>(`/api/mcp/servers/${serverId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function refreshMcpServer(serverId: string): Promise<MCPServer> {
  return apiRequest<MCPServer>(`/api/mcp/servers/${serverId}/refresh`, {
    method: "POST",
  });
}

export async function getModelApiSettings(signal?: AbortSignal): Promise<ModelApiSettings> {
  return apiRequest<ModelApiSettings>("/api/settings/model-api", { signal });
}

export async function updateModelApiSettings(payload: ModelApiSettingsUpdate): Promise<void> {
  return apiRequest<void>("/api/settings/model-api", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
