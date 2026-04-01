import type {
  AttachmentMetadata,
  ChatResponse,
  SessionDetail,
  SessionSummary,
} from "../types/sessions";
import type {
  ProjectCreateRequest,
  ProjectDetail,
  ProjectSummary,
  ProjectUpdateRequest,
} from "../types/projects";
import type { SessionHistoryEntry } from "../types/history";
import type { SessionGraph } from "../types/graphs";
import type {
  WorkflowRunDetail,
  WorkflowRunExport,
  WorkflowRunReplay,
  WorkflowStartRequest,
  WorkflowTaskPriorityReorderRequest,
  WorkflowTemplate,
} from "../types/workflows";
import type {
  RuntimeArtifact,
  RuntimeArtifactsCleanupResult,
  RuntimeExecuteRequest,
  RuntimeExecutionRun,
  RuntimeHealth,
  RuntimeProfile,
  RuntimeState,
  RuntimeStatusResponse,
} from "../types/runtime";
import type {
  MCPServer,
  MCPToolInvokeRequest,
  MCPToolInvokeResponse,
  ManualMCPServerRegisterRequest,
} from "../types/mcp";
import type { ModelApiSettings, ModelApiSettingsUpdate } from "../types/settings";
import type { SkillContent, SkillContext, SkillRecord } from "../types/skills";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);
const apiToken = import.meta.env.VITE_API_TOKEN as string | undefined;

type ApiEnvelope<T> = {
  data: T;
  meta?: {
    request_id?: string | null;
    pagination?: {
      page: number;
      page_size: number;
      total: number;
    } | null;
    sort?: {
      by: string;
      direction: string;
    } | null;
  } | null;
};

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

  if (payload && typeof payload === "object" && "error" in payload) {
    const error = payload.error;
    if (
      error &&
      typeof error === "object" &&
      "message" in error &&
      typeof error.message === "string"
    ) {
      return error.message;
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
  const isFormDataBody = typeof FormData !== "undefined" && init?.body instanceof FormData;

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body && !isFormDataBody ? { "Content-Type": "application/json" } : {}),
      ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = (await response.json()) as ApiEnvelope<T> | T;
  if (payload && typeof payload === "object" && "data" in payload) {
    return payload.data;
  }
  return payload as T;
}

function buildQueryString(
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const searchParams = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") {
      continue;
    }

    searchParams.set(key, String(value));
  }

  const query = searchParams.toString();
  return query ? `?${query}` : "";
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

export async function getSessionHistory(
  sessionId: string,
  params: {
    page?: number;
    page_size?: number;
    level?: string | null;
    source?: string | null;
    event_type?: string | null;
    q?: string | null;
    sort_order?: "asc" | "desc";
  } = {},
  signal?: AbortSignal,
): Promise<SessionHistoryEntry[]> {
  return apiRequest<SessionHistoryEntry[]>(
    `/api/sessions/${sessionId}/history${buildQueryString(params)}`,
    { signal },
  );
}

export async function getSessionArtifacts(
  sessionId: string,
  params: {
    page?: number;
    page_size?: number;
    q?: string | null;
    sort_by?: "created_at" | "relative_path";
    sort_order?: "asc" | "desc";
  } = {},
  signal?: AbortSignal,
): Promise<RuntimeArtifact[]> {
  return apiRequest<RuntimeArtifact[]>(
    `/api/sessions/${sessionId}/artifacts${buildQueryString(params)}`,
    { signal },
  );
}

export async function listProjects(
  params: {
    include_deleted?: boolean;
    q?: string | null;
    page?: number;
    page_size?: number;
    sort_by?: "updated_at" | "created_at" | "name";
    sort_order?: "asc" | "desc";
  } = {},
  signal?: AbortSignal,
): Promise<ProjectSummary[]> {
  return apiRequest<ProjectSummary[]>(`/api/projects${buildQueryString(params)}`, { signal });
}

export async function getProject(projectId: string, signal?: AbortSignal): Promise<ProjectDetail> {
  return apiRequest<ProjectDetail>(`/api/projects/${projectId}`, { signal });
}

export async function createProject(payload: ProjectCreateRequest): Promise<ProjectSummary> {
  return apiRequest<ProjectSummary>("/api/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateProject(
  projectId: string,
  payload: ProjectUpdateRequest,
): Promise<ProjectSummary> {
  return apiRequest<ProjectSummary>(`/api/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteProject(projectId: string): Promise<void> {
  return apiRequest<void>(`/api/projects/${projectId}`, {
    method: "DELETE",
  });
}

export async function restoreProject(projectId: string): Promise<ProjectSummary> {
  return apiRequest<ProjectSummary>(`/api/projects/${projectId}/restore`, {
    method: "POST",
  });
}

export async function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionDetail> {
  return apiRequest<SessionDetail>(`/api/sessions/${sessionId}`, { signal });
}

export async function getTaskGraph(sessionId: string, signal?: AbortSignal): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/task`, { signal });
}

export async function getCausalGraph(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/causal`, { signal });
}

export async function getEvidenceGraph(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/evidence`, { signal });
}

export async function getTaskGraphForRun(
  runId: string,
  signal?: AbortSignal,
): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/workflows/${runId}/graphs/task`, { signal });
}

export async function getCausalGraphForRun(
  runId: string,
  signal?: AbortSignal,
): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/workflows/${runId}/graphs/causal`, { signal });
}

export async function getEvidenceGraphForRun(
  runId: string,
  signal?: AbortSignal,
): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/workflows/${runId}/graphs/evidence`, { signal });
}

export async function listWorkflowTemplates(signal?: AbortSignal): Promise<WorkflowTemplate[]> {
  return apiRequest<WorkflowTemplate[]>("/api/workflows/templates", { signal });
}

export async function startWorkflow(payload: WorkflowStartRequest): Promise<WorkflowRunDetail> {
  return apiRequest<WorkflowRunDetail>("/api/workflows/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getWorkflow(runId: string, signal?: AbortSignal): Promise<WorkflowRunDetail> {
  return apiRequest<WorkflowRunDetail>(`/api/workflows/${runId}`, { signal });
}

export async function advanceWorkflow(
  runId: string,
  payload: { approve?: boolean } = {},
): Promise<WorkflowRunDetail> {
  return apiRequest<WorkflowRunDetail>(`/api/workflows/${runId}/advance`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function reorderWorkflowTaskPriorities(
  runId: string,
  payload: WorkflowTaskPriorityReorderRequest,
): Promise<WorkflowRunDetail> {
  return apiRequest<WorkflowRunDetail>(`/api/workflows/${runId}/tasks/reorder-priority`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getWorkflowExport(
  runId: string,
  signal?: AbortSignal,
): Promise<WorkflowRunExport> {
  return apiRequest<WorkflowRunExport>(`/api/workflows/${runId}/export`, { signal });
}

export async function getWorkflowReplay(
  runId: string,
  signal?: AbortSignal,
): Promise<WorkflowRunReplay> {
  return apiRequest<WorkflowRunReplay>(`/api/workflows/${runId}/replay`, { signal });
}

export async function createSession(title?: string): Promise<SessionSummary> {
  return apiRequest<SessionSummary>("/api/sessions", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });
}

export async function updateSession(
  sessionId: string,
  payload: Partial<
    Pick<
      SessionSummary,
      "title" | "status" | "project_id" | "goal" | "scenario_type" | "current_phase"
    > & {
      runtime_policy_json: SessionSummary["runtime_policy_json"];
    }
  >,
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

export async function cancelSession(sessionId: string): Promise<SessionSummary> {
  return apiRequest<SessionSummary>(`/api/sessions/${sessionId}/cancel`, {
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

export async function getSkillContent(
  skillId: string,
  signal?: AbortSignal,
): Promise<SkillContent> {
  return apiRequest<SkillContent>(`/api/skills/${skillId}/content`, { signal });
}

export async function getSkillContext(signal?: AbortSignal): Promise<SkillContext> {
  return apiRequest<SkillContext>("/api/skills/skill-context", { signal });
}

export async function rescanSkills(): Promise<SkillRecord[]> {
  return apiRequest<SkillRecord[]>("/api/skills/rescan", {
    method: "POST",
  });
}

export async function scanSkills(): Promise<SkillRecord[]> {
  return apiRequest<SkillRecord[]>("/api/skills/scan", {
    method: "POST",
  });
}

export async function refreshSkills(): Promise<SkillRecord[]> {
  return apiRequest<SkillRecord[]>("/api/skills/refresh", {
    method: "POST",
  });
}

export async function toggleSkill(skillId: string, enabled: boolean): Promise<SkillRecord> {
  return apiRequest<SkillRecord>(`/api/skills/${skillId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function setSkillEnabled(skillId: string, enabled: boolean): Promise<SkillRecord> {
  return apiRequest<SkillRecord>(`/api/skills/${skillId}/${enabled ? "enable" : "disable"}`, {
    method: "POST",
  });
}

export async function getRuntimeStatus(signal?: AbortSignal): Promise<RuntimeStatusResponse> {
  return apiRequest<RuntimeStatusResponse>("/api/runtime/status", { signal });
}

export async function getRuntimeHealth(signal?: AbortSignal): Promise<RuntimeHealth> {
  return apiRequest<RuntimeHealth>("/api/runtime/health", { signal });
}

export async function listRuntimeRuns(
  params: {
    page?: number;
    page_size?: number;
    q?: string | null;
    session_id?: string | null;
    sort_by?: "started_at" | "created_at";
    sort_order?: "asc" | "desc";
  } = {},
  signal?: AbortSignal,
): Promise<RuntimeExecutionRun[]> {
  return apiRequest<RuntimeExecutionRun[]>(`/api/runtime/runs${buildQueryString(params)}`, {
    signal,
  });
}

export async function listRuntimeArtifacts(
  params: {
    page?: number;
    page_size?: number;
    q?: string | null;
    session_id?: string | null;
    sort_by?: "created_at" | "relative_path";
    sort_order?: "asc" | "desc";
  } = {},
  signal?: AbortSignal,
): Promise<RuntimeArtifact[]> {
  return apiRequest<RuntimeArtifact[]>(`/api/runtime/artifacts${buildQueryString(params)}`, {
    signal,
  });
}

export async function listRuntimeProfiles(signal?: AbortSignal): Promise<RuntimeProfile[]> {
  return apiRequest<RuntimeProfile[]>("/api/runtime/profiles", { signal });
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

export async function uploadRuntimeArtifact(payload: {
  file: File;
  path: string;
  session_id?: string | null;
  overwrite?: boolean;
}): Promise<RuntimeExecutionRun> {
  const formData = new FormData();
  formData.set("file", payload.file);
  formData.set("path", payload.path);

  if (payload.session_id) {
    formData.set("session_id", payload.session_id);
  }

  formData.set("overwrite", payload.overwrite ? "true" : "false");

  return apiRequest<RuntimeExecutionRun>("/api/runtime/upload", {
    method: "POST",
    body: formData,
  });
}

export async function downloadRuntimeArtifact(path: string): Promise<Blob> {
  const response = await fetch(`${apiBaseUrl}/api/runtime/download${buildQueryString({ path })}`, {
    headers: {
      Accept: "application/octet-stream",
      ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
    },
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  return response.blob();
}

export async function cleanupRuntimeArtifacts(): Promise<RuntimeArtifactsCleanupResult> {
  return apiRequest<RuntimeArtifactsCleanupResult>("/api/runtime/artifacts/cleanup", {
    method: "POST",
  });
}

export async function importMcpServers(): Promise<MCPServer[]> {
  return apiRequest<MCPServer[]>("/api/mcp/import", {
    method: "POST",
  });
}

export async function registerManualMcpServer(
  payload: ManualMCPServerRegisterRequest,
): Promise<MCPServer> {
  return apiRequest<MCPServer>("/api/mcp/register", {
    method: "POST",
    body: JSON.stringify(payload),
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

export async function setMcpServerEnabled(serverId: string, enabled: boolean): Promise<MCPServer> {
  return apiRequest<MCPServer>(`/api/mcp/servers/${serverId}/${enabled ? "enable" : "disable"}`, {
    method: "POST",
  });
}

export async function refreshMcpServer(serverId: string): Promise<MCPServer> {
  return apiRequest<MCPServer>(`/api/mcp/servers/${serverId}/refresh`, {
    method: "POST",
  });
}

export async function checkMcpServerHealth(serverId: string): Promise<MCPServer> {
  return apiRequest<MCPServer>(`/api/mcp/servers/${serverId}/health`, {
    method: "POST",
  });
}

export async function invokeMcpTool(
  serverId: string,
  toolName: string,
  payload: MCPToolInvokeRequest,
): Promise<MCPToolInvokeResponse> {
  return apiRequest<MCPToolInvokeResponse>(
    `/api/mcp/servers/${serverId}/tools/${toolName}/invoke`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
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
