import type {
  ActiveGenerationInjectResponse,
  AttachmentMetadata,
  ChatGeneration,
  ChatResponse,
  SessionCompactionResult,
  ConversationBranch,
  SessionContextWindowUsage,
  SessionConversation,
  SessionDetail,
  SessionQueue,
  SessionReplay,
  SessionSummary,
} from "../types/sessions";
import type { SessionHistoryEntry } from "../types/history";
import type { SessionGraph } from "../types/graphs";
import type {
  RuntimeArtifact,
  RuntimeArtifactsCleanupResult,
  RuntimeExecuteRequest,
  RuntimeExecutionRun,
  RuntimeHealth,
  RuntimeProfile,
  RuntimeRunsClearResult,
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
import type { SlashAction, SlashCatalogItem } from "../types/slash";
import type { SkillContent, SkillContext, SkillRecord } from "../types/skills";
import type {
  TerminalExecuteResponse,
  TerminalJob,
  TerminalJobsCleanupResult,
  TerminalJobTail,
  TerminalSession,
} from "../types/terminals";

function resolveDefaultApiBaseUrl(): string {
  if (typeof window === "undefined") {
    return "http://0.0.0.0:8000";
  }

  const { protocol, hostname } = window.location;
  const normalizedHostname = hostname.trim();
  const resolvedHostname =
    normalizedHostname.length === 0 || normalizedHostname === "localhost"
      ? "127.0.0.1"
      : normalizedHostname;
  const resolvedProtocol = protocol === "https:" ? "https:" : "http:";

  return `${resolvedProtocol}//${resolvedHostname}:8000`;
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? resolveDefaultApiBaseUrl()).replace(
  /\/$/,
  "",
);
const apiToken = import.meta.env.VITE_API_TOKEN as string | undefined;
const API_BASIC_AUTH_STORAGE_KEY = "aegissec.api.basic_auth";
export const API_AUTH_EXPIRED_EVENT = "aegissec.api.auth-expired";

function readStoredBasicAuthToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const rawValue = window.localStorage.getItem(API_BASIC_AUTH_STORAGE_KEY);
    if (!rawValue) {
      return null;
    }

    const parsedValue = JSON.parse(rawValue) as { token?: unknown };
    if (typeof parsedValue.token !== "string") {
      return null;
    }

    const normalizedToken = parsedValue.token.trim();
    return normalizedToken.length > 0 ? normalizedToken : null;
  } catch {
    return null;
  }
}

function persistBasicAuthToken(token: string | null): void {
  if (typeof window === "undefined") {
    return;
  }

  if (token === null) {
    window.localStorage.removeItem(API_BASIC_AUTH_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(
    API_BASIC_AUTH_STORAGE_KEY,
    JSON.stringify({ token }),
  );
}

function emitAuthExpiredEvent(): void {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(new CustomEvent(API_AUTH_EXPIRED_EVENT));
}

function encodeBase64(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";

  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }

  return btoa(binary);
}

let runtimeBasicAuthToken: string | null = readStoredBasicAuthToken();

function getAuthorizationHeader(): string | null {
  if (runtimeBasicAuthToken) {
    return `Basic ${runtimeBasicAuthToken}`;
  }

  const normalizedApiToken = (apiToken ?? "").trim();
  if (normalizedApiToken.length > 0) {
    return `Bearer ${normalizedApiToken}`;
  }

  return null;
}

export function hasApiBasicCredentials(): boolean {
  return runtimeBasicAuthToken !== null;
}

export function setApiBasicCredentials(username: string, password: string): void {
  runtimeBasicAuthToken = encodeBase64(`${username}:${password}`);
  persistBasicAuthToken(runtimeBasicAuthToken);
}

export function clearApiBasicCredentials(emitAuthExpired = false): void {
  runtimeBasicAuthToken = null;
  persistBasicAuthToken(null);

  if (emitAuthExpired) {
    emitAuthExpiredEvent();
  }
}

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

type ApiErrorOptions = {
  message: string;
  status: number;
  statusText: string;
  path: string;
  body?: unknown;
};

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly path: string;
  readonly body: unknown;

  constructor({ message, status, statusText, path, body = null }: ApiErrorOptions) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.path = path;
    this.body = body;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export type AuthStatusRead = {
  mode: string;
  token_required: boolean;
};

type AuthLoginPayload = {
  username: string;
  password: string;
};

export type AuthLoginRead = {
  mode: string;
  authenticated: boolean;
};

type ChatRequestPayload = {
  content: string;
  slash_action?: SlashAction | null;
  attachments?: AttachmentMetadata[];
  branch_id?: string | null;
  parent_message_id?: string | null;
  token_budget?: number | null;
  wait_for_completion?: boolean;
};

type ActiveGenerationInjectRequest = {
  content: string;
};

type MessageEditPayload = {
  content: string;
  attachments?: AttachmentMetadata[];
  branch_id?: string | null;
  token_budget?: number | null;
  wait_for_completion?: boolean;
};

type MessageRegeneratePayload = {
  branch_id?: string | null;
  token_budget?: number | null;
  wait_for_completion?: boolean;
};

type BranchForkPayload = {
  name?: string | null;
};

type MessageRollbackPayload = {
  branch_id?: string | null;
};

function readErrorMessageFromPayload(payload: unknown, fallbackMessage: string): string {
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

  return fallbackMessage;
}

async function readErrorResponse(response: Response): Promise<{ message: string; body: unknown }> {
  const fallbackMessage = `Request failed (HTTP ${response.status})`;
  const clonedResponse = response.clone();
  const payload = await clonedResponse.json().catch((): unknown => null);

  const payloadMessage = readErrorMessageFromPayload(payload, fallbackMessage);
  if (payloadMessage !== fallbackMessage) {
    return {
      message: payloadMessage,
      body: payload,
    };
  }

  try {
    const text = await response.text();
    return {
      message: text || fallbackMessage,
      body: payload ?? (text || null),
    };
  } catch {
    return {
      message: fallbackMessage,
      body: payload,
    };
  }
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormDataBody = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const authorizationHeader = getAuthorizationHeader();

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body && !isFormDataBody ? { "Content-Type": "application/json" } : {}),
      ...(authorizationHeader ? { Authorization: authorizationHeader } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    if (
      response.status === 401 &&
      runtimeBasicAuthToken !== null &&
      !path.startsWith("/api/auth/login")
    ) {
      clearApiBasicCredentials(true);
    }

    const { message, body } = await readErrorResponse(response);
    throw new ApiError({
      message,
      status: response.status,
      statusText: response.statusText,
      path,
      body,
    });
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

export async function getAuthStatus(signal?: AbortSignal): Promise<AuthStatusRead> {
  return apiRequest<AuthStatusRead>("/api/auth/status", { signal });
}

export async function loginWithCredentials(payload: AuthLoginPayload): Promise<AuthLoginRead> {
  return apiRequest<AuthLoginRead>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
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

export async function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionDetail> {
  return apiRequest<SessionDetail>(`/api/sessions/${sessionId}`, { signal });
}

export async function getSessionConversation(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionConversation> {
  return apiRequest<SessionConversation>(`/api/sessions/${sessionId}/conversation`, { signal });
}

export async function getSessionQueue(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionQueue> {
  return apiRequest<SessionQueue>(`/api/sessions/${sessionId}/queue`, { signal });
}

export async function getSessionContextWindowUsage(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionContextWindowUsage> {
  return apiRequest<SessionContextWindowUsage>(`/api/sessions/${sessionId}/context-window`, {
    signal,
  });
}

export async function compactSessionContext(sessionId: string): Promise<SessionCompactionResult> {
  return apiRequest<SessionCompactionResult>(`/api/sessions/${sessionId}/compact`, {
    method: "POST",
    body: JSON.stringify({ mode: "manual" }),
  });
}

export async function getSessionSlashCatalog(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SlashCatalogItem[]> {
  return apiRequest<SlashCatalogItem[]>(`/api/sessions/${sessionId}/slash-catalog`, { signal });
}

export async function getSessionReplay(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionReplay> {
  return apiRequest<SessionReplay>(`/api/sessions/${sessionId}/replay`, { signal });
}

export async function getTaskGraph(sessionId: string, signal?: AbortSignal): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/task`, { signal });
}

export async function getAttackGraph(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionGraph> {
  return apiRequest<SessionGraph>(`/api/sessions/${sessionId}/graphs/attack`, { signal });
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
      | "title"
      | "status"
      | "project_id"
      | "active_branch_id"
      | "goal"
      | "scenario_type"
      | "current_phase"
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

export async function injectActiveGenerationContext(
  sessionId: string,
  payload: ActiveGenerationInjectRequest,
): Promise<ActiveGenerationInjectResponse> {
  return apiRequest<ActiveGenerationInjectResponse>(
    `/api/sessions/${sessionId}/generations/active/inject`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function editSessionMessage(
  sessionId: string,
  messageId: string,
  payload: MessageEditPayload,
): Promise<{
  session: SessionSummary;
  branch: ConversationBranch;
  user_message?: NonNullable<ChatResponse["user_message"]> | null;
  assistant_message?: NonNullable<ChatResponse["assistant_message"]> | null;
  generation?: ChatGeneration | null;
}> {
  return apiRequest(`/api/sessions/${sessionId}/messages/${messageId}/edit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function regenerateSessionMessage(
  sessionId: string,
  messageId: string,
  payload: MessageRegeneratePayload = {},
): Promise<{
  session: SessionSummary;
  branch: ConversationBranch;
  user_message?: NonNullable<ChatResponse["user_message"]> | null;
  assistant_message?: NonNullable<ChatResponse["assistant_message"]> | null;
  generation?: ChatGeneration | null;
}> {
  return apiRequest(`/api/sessions/${sessionId}/messages/${messageId}/regenerate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function forkSessionMessage(
  sessionId: string,
  messageId: string,
  payload: BranchForkPayload = {},
): Promise<SessionConversation> {
  return apiRequest(`/api/sessions/${sessionId}/messages/${messageId}/fork`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function rollbackSessionMessage(
  sessionId: string,
  messageId: string,
  payload: MessageRollbackPayload = {},
): Promise<SessionConversation> {
  return apiRequest(`/api/sessions/${sessionId}/messages/${messageId}/rollback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function cancelGeneration(
  sessionId: string,
  generationId: string,
): Promise<ChatGeneration> {
  return apiRequest(`/api/sessions/${sessionId}/generations/${generationId}/cancel`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function getSessionEventsUrl(sessionId: string, cursor?: number | null): string {
  const queryParams: Record<string, string | number | boolean | null | undefined> = {
    cursor,
  };

  if (runtimeBasicAuthToken !== null) {
    queryParams.auth_basic = runtimeBasicAuthToken;
  } else if (apiToken && apiToken.trim().length > 0) {
    queryParams.token = apiToken.trim();
  }

  return `${apiBaseUrl.replace(/^http/, "ws")}/api/sessions/${sessionId}/events${buildQueryString(queryParams)}`;
}

export async function listSessionTerminals(
  sessionId: string,
  signal?: AbortSignal,
): Promise<TerminalSession[]> {
  return apiRequest<TerminalSession[]>(`/api/sessions/${sessionId}/terminals`, { signal });
}

export async function createSessionTerminal(
  sessionId: string,
  payload: {
    title?: string;
    shell?: string;
    cwd?: string;
    metadata?: Record<string, unknown>;
  },
): Promise<TerminalSession> {
  return apiRequest<TerminalSession>(`/api/sessions/${sessionId}/terminals`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getSessionTerminal(
  sessionId: string,
  terminalId: string,
  signal?: AbortSignal,
): Promise<TerminalSession> {
  return apiRequest<TerminalSession>(`/api/sessions/${sessionId}/terminals/${terminalId}`, {
    signal,
  });
}

export async function closeSessionTerminal(
  sessionId: string,
  terminalId: string,
): Promise<TerminalSession> {
  return apiRequest<TerminalSession>(
    `/api/sessions/${sessionId}/terminals/${terminalId}/close`,
    {
      method: "POST",
    },
  );
}

export async function sendTerminalInput(
  sessionId: string,
  terminalId: string,
  data: string,
): Promise<void> {
  return apiRequest<void>(`/api/sessions/${sessionId}/terminals/${terminalId}/input`, {
    method: "POST",
    body: JSON.stringify({ data }),
  });
}

export async function resizeTerminal(
  sessionId: string,
  terminalId: string,
  cols: number,
  rows: number,
): Promise<void> {
  return apiRequest<void>(`/api/sessions/${sessionId}/terminals/${terminalId}/resize`, {
    method: "POST",
    body: JSON.stringify({ cols, rows }),
  });
}

export async function interruptTerminal(
  sessionId: string,
  terminalId: string,
): Promise<void> {
  return apiRequest<void>(`/api/sessions/${sessionId}/terminals/${terminalId}/interrupt`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function executeTerminalCommand(
  sessionId: string,
  terminalId: string,
  payload: {
    command: string;
    detach?: boolean;
    timeout_seconds?: number | null;
    artifact_paths?: string[];
  },
): Promise<TerminalExecuteResponse> {
  return apiRequest<TerminalExecuteResponse>(
    `/api/sessions/${sessionId}/terminals/${terminalId}/execute`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export async function listSessionTerminalJobs(
  sessionId: string,
  signal?: AbortSignal,
): Promise<TerminalJob[]> {
  return apiRequest<TerminalJob[]>(`/api/sessions/${sessionId}/terminal-jobs`, { signal });
}

export async function getSessionTerminalJob(
  sessionId: string,
  jobId: string,
  signal?: AbortSignal,
): Promise<TerminalJob> {
  return apiRequest<TerminalJob>(`/api/sessions/${sessionId}/terminal-jobs/${jobId}`, { signal });
}

export async function tailSessionTerminalJob(
  sessionId: string,
  jobId: string,
  params: { stream?: "stdout" | "stderr"; lines?: number } = {},
  signal?: AbortSignal,
): Promise<TerminalJobTail> {
  return apiRequest<TerminalJobTail>(
    `/api/sessions/${sessionId}/terminal-jobs/${jobId}/tail${buildQueryString(params)}`,
    { signal },
  );
}

export async function stopSessionTerminalJob(
  sessionId: string,
  jobId: string,
): Promise<TerminalJob> {
  return apiRequest<TerminalJob>(`/api/sessions/${sessionId}/terminal-jobs/${jobId}/stop`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function cleanupSessionTerminalJobs(
  sessionId: string,
  payload: { limit?: number | null } = {},
): Promise<TerminalJobsCleanupResult> {
  return apiRequest<TerminalJobsCleanupResult>(`/api/sessions/${sessionId}/terminal-jobs/cleanup`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSessionTerminalStreamUrl(sessionId: string, terminalId: string): string {
  return `${apiBaseUrl.replace(/^http/, "ws")}/api/sessions/${sessionId}/terminals/${terminalId}/stream`;
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
  const authorizationHeader = getAuthorizationHeader();
  const response = await fetch(`${apiBaseUrl}/api/runtime/download${buildQueryString({ path })}`, {
    headers: {
      Accept: "application/octet-stream",
      ...(authorizationHeader ? { Authorization: authorizationHeader } : {}),
    },
  });

  if (!response.ok) {
    if (response.status === 401 && runtimeBasicAuthToken !== null) {
      clearApiBasicCredentials(true);
    }

    const { message, body } = await readErrorResponse(response);
    throw new ApiError({
      message,
      status: response.status,
      statusText: response.statusText,
      path: `/api/runtime/download${buildQueryString({ path })}`,
      body,
    });
  }

  return response.blob();
}

export async function cleanupRuntimeArtifacts(): Promise<RuntimeArtifactsCleanupResult> {
  return apiRequest<RuntimeArtifactsCleanupResult>("/api/runtime/artifacts/cleanup", {
    method: "POST",
  });
}

export async function clearRuntimeRuns(): Promise<RuntimeRunsClearResult> {
  return apiRequest<RuntimeRunsClearResult>("/api/runtime/runs/clear", {
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

export async function deleteMcpServer(serverId: string): Promise<void> {
  return apiRequest<void>(`/api/mcp/servers/${serverId}`, {
    method: "DELETE",
  });
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
