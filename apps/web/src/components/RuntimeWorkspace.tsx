import { type FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cleanupRuntimeArtifacts,
  downloadRuntimeArtifact,
  executeRuntimeCommand,
  getRuntimeHealth,
  getRuntimeStatus,
  listRuntimeArtifacts,
  listRuntimeProfiles,
  listRuntimeRuns,
  listSessions,
  startRuntime,
  stopRuntime,
  uploadRuntimeArtifact,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import type {
  RuntimeArtifact,
  RuntimeArtifactsCleanupResult,
  RuntimeExecuteRequest,
  RuntimeExecutionRun,
  RuntimeState,
  RuntimeStatusResponse,
} from "../types/runtime";
import type { SessionSummary } from "../types/sessions";
import { StatusBadge } from "./StatusBadge";

const RUNTIME_STATUS_QUERY_KEY = ["runtime-status"] as const;
const RUNTIME_HEALTH_QUERY_KEY = ["runtime-health"] as const;
const RUNTIME_RUNS_QUERY_KEY = ["runtime-runs"] as const;
const RUNTIME_ARTIFACTS_QUERY_KEY = ["runtime-artifacts"] as const;
const RUNTIME_PROFILES_QUERY_KEY = ["runtime-profiles"] as const;
const RUNTIME_SESSIONS_QUERY_KEY = ["sessions", "runtime-workspace"] as const;

function formatOptionalDateTime(value: string | null): string {
  return value ? formatDateTime(value) : "未启动";
}

function formatOptionalValue(value: string | null): string {
  return value && value.trim().length > 0 ? value : "暂未提供";
}

function parseArtifactPaths(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((path) => path.trim())
    .filter((path) => path.length > 0);
}

function stringifyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function getSessionStatusTone(status: string): string {
  switch (status) {
    case "running":
      return "tone-connected";
    case "done":
      return "tone-success";
    case "error":
      return "tone-error";
    case "paused":
      return "tone-warning";
    default:
      return "tone-neutral";
  }
}

function getHealthTone(status: string | null): string {
  switch (status) {
    case "ok":
      return "tone-success";
    case "degraded":
      return "tone-warning";
    case "error":
      return "tone-error";
    default:
      return "tone-neutral";
  }
}

function getDownloadFileName(path: string): string {
  const normalizedPath = path.replace(/\\/g, "/");
  const segments = normalizedPath.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? "artifact.bin";
}

function triggerArtifactDownload(blob: Blob, fileName: string): void {
  const objectUrl = window.URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = objectUrl;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function updateRuntimeState(
  currentValue: RuntimeStatusResponse | undefined,
  runtime: RuntimeState,
): RuntimeStatusResponse {
  if (!currentValue) {
    return {
      runtime,
      recent_runs: [],
      recent_artifacts: [],
    };
  }

  return {
    ...currentValue,
    runtime,
  };
}

function addRecentRun(
  currentValue: RuntimeStatusResponse | undefined,
  run: RuntimeExecutionRun,
): RuntimeStatusResponse | undefined {
  if (!currentValue) {
    return currentValue;
  }

  return {
    ...currentValue,
    recent_runs: [
      run,
      ...currentValue.recent_runs.filter((currentRun) => currentRun.id !== run.id),
    ],
    recent_artifacts: [
      ...run.artifacts,
      ...currentValue.recent_artifacts.filter(
        (artifact) => !run.artifacts.some((runArtifact) => runArtifact.id === artifact.id),
      ),
    ],
  };
}

function RuntimeArtifactList({
  artifacts,
  downloadingPath,
  onDownload,
}: {
  artifacts: RuntimeArtifact[];
  downloadingPath: string | null;
  onDownload: (artifact: RuntimeArtifact) => void;
}) {
  if (artifacts.length === 0) {
    return <div className="management-inline-notice">这次执行没有登记工件。</div>;
  }

  return (
    <ul className="management-list">
      {artifacts.map((artifact) => (
        <li key={artifact.id} className="management-subcard">
          <div className="management-list-card-header">
            <strong className="management-list-title">{artifact.relative_path}</strong>
            <div className="management-action-row">
              <span className="management-status-badge tone-neutral">
                {formatDateTime(artifact.created_at)}
              </span>
              <button
                className="button button-secondary"
                type="button"
                disabled={downloadingPath === artifact.relative_path}
                onClick={() => onDownload(artifact)}
              >
                {downloadingPath === artifact.relative_path ? "下载中" : "下载"}
              </button>
            </div>
          </div>

          <div className="management-info-grid">
            <div className="management-info-card management-info-card-full">
              <span className="management-info-label">宿主机路径</span>
              <strong className="management-info-value management-info-code">
                {artifact.host_path}
              </strong>
            </div>
            <div className="management-info-card management-info-card-full">
              <span className="management-info-label">容器路径</span>
              <strong className="management-info-value management-info-code">
                {artifact.container_path}
              </strong>
            </div>
            <div className="management-info-card">
              <span className="management-info-label">Run</span>
              <strong className="management-info-value management-info-code">
                {artifact.run_id}
              </strong>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function SessionPolicySection({
  activeSession,
  isLoading,
  requestedSessionId,
}: {
  activeSession: SessionSummary | null;
  isLoading: boolean;
  requestedSessionId: string;
}) {
  const policy = activeSession?.runtime_policy_json ?? null;
  const hasPolicy = Boolean(policy && Object.keys(policy).length > 0);

  return (
    <section className="management-section-card">
      <div className="management-section-header">
        <h3 className="management-section-title">运行策略</h3>
        <span className="management-status-badge tone-neutral">
          {activeSession ? activeSession.id : requestedSessionId || "未绑定对话"}
        </span>
      </div>

      {isLoading && requestedSessionId ? (
        <div className="management-inline-notice">正在读取该对话的 runtime policy。</div>
      ) : !requestedSessionId ? (
        <div className="management-inline-notice">
          填入对话 ID，或使用最近已绑定的执行记录后，这里会显示对应策略。
        </div>
      ) : !activeSession ? (
        <div className="management-error-banner">未找到该对话，无法展示策略详情。</div>
      ) : (
        <>
          <div className="management-info-grid">
            <div className="management-info-card">
              <span className="management-info-label">对话标题</span>
              <strong className="management-info-value">{activeSession.title}</strong>
            </div>
            <div className="management-info-card">
              <span className="management-info-label">当前阶段</span>
              <strong className="management-info-value">
                {activeSession.current_phase ?? "未开始"}
              </strong>
            </div>
            <div className="management-info-card">
              <span className="management-info-label">对话状态</span>
              <strong
                className={`management-status-badge ${getSessionStatusTone(activeSession.status)}`}
              >
                {activeSession.status}
              </strong>
            </div>
            <div className="management-info-card management-info-card-full">
              <span className="management-info-label">对话 ID</span>
              <strong className="management-info-value management-info-code">
                {activeSession.id}
              </strong>
            </div>
          </div>

          {hasPolicy ? (
            <div className="management-subcard">
              <span className="management-info-label">策略 JSON</span>
              <pre className="management-code-block">{stringifyJson(policy)}</pre>
            </div>
          ) : (
            <div className="management-inline-notice">该对话当前没有附带 runtime policy。</div>
          )}
        </>
      )}
    </section>
  );
}

export function RuntimeWorkspace() {
  const queryClient = useQueryClient();
  const [command, setCommand] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [artifactPaths, setArtifactPaths] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadPath, setUploadPath] = useState("");
  const [uploadSessionId, setUploadSessionId] = useState("");
  const [uploadOverwrite, setUploadOverwrite] = useState(false);
  const [uploadInputResetKey, setUploadInputResetKey] = useState(0);
  const [downloadingPath, setDownloadingPath] = useState<string | null>(null);

  const runtimeStatusQuery = useQuery({
    queryKey: RUNTIME_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => getRuntimeStatus(signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: 15000,
  });

  const runtimeHealthQuery = useQuery({
    queryKey: RUNTIME_HEALTH_QUERY_KEY,
    queryFn: ({ signal }) => getRuntimeHealth(signal),
    placeholderData: (previousValue) => previousValue,
    refetchInterval: 15000,
  });

  const runtimeRunsQuery = useQuery({
    queryKey: RUNTIME_RUNS_QUERY_KEY,
    queryFn: ({ signal }) => listRuntimeRuns({ page_size: 20 }, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const runtimeArtifactsQuery = useQuery({
    queryKey: RUNTIME_ARTIFACTS_QUERY_KEY,
    queryFn: ({ signal }) => listRuntimeArtifacts({ page_size: 20 }, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const runtimeProfilesQuery = useQuery({
    queryKey: RUNTIME_PROFILES_QUERY_KEY,
    queryFn: ({ signal }) => listRuntimeProfiles(signal),
    placeholderData: (previousValue) => previousValue,
  });

  const sessionsQuery = useQuery({
    queryKey: RUNTIME_SESSIONS_QUERY_KEY,
    queryFn: ({ signal }) => listSessions(true, signal),
    placeholderData: (previousValue) => previousValue,
  });

  const startRuntimeMutation = useMutation({
    mutationFn: () => startRuntime(),
    onSuccess: async (runtime) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(
        RUNTIME_STATUS_QUERY_KEY,
        (currentValue) => updateRuntimeState(currentValue, runtime),
      );
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_HEALTH_QUERY_KEY });
    },
  });

  const stopRuntimeMutation = useMutation({
    mutationFn: () => stopRuntime(),
    onSuccess: async (runtime) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(
        RUNTIME_STATUS_QUERY_KEY,
        (currentValue) => updateRuntimeState(currentValue, runtime),
      );
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_HEALTH_QUERY_KEY });
    },
  });

  const executeRuntimeMutation = useMutation({
    mutationFn: (payload: RuntimeExecuteRequest) => executeRuntimeCommand(payload),
    onSuccess: async (run) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(
        RUNTIME_STATUS_QUERY_KEY,
        (currentValue) => addRecentRun(currentValue, run),
      );
      setCommand("");
      setArtifactPaths("");
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_RUNS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_ARTIFACTS_QUERY_KEY });
    },
  });

  const uploadRuntimeMutation = useMutation({
    mutationFn: (payload: {
      file: File;
      path: string;
      session_id?: string | null;
      overwrite?: boolean;
    }) => uploadRuntimeArtifact(payload),
    onSuccess: async (run) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(
        RUNTIME_STATUS_QUERY_KEY,
        (currentValue) => addRecentRun(currentValue, run),
      );
      setUploadFile(null);
      setUploadInputResetKey((currentValue) => currentValue + 1);
      setUploadPath("");
      setUploadSessionId("");
      setUploadOverwrite(false);
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_RUNS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_ARTIFACTS_QUERY_KEY });
    },
  });

  const cleanupArtifactsMutation = useMutation({
    mutationFn: () => cleanupRuntimeArtifacts(),
    onSuccess: async (result: RuntimeArtifactsCleanupResult) => {
      if (result.deleted_rows > 0) {
        await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
        await queryClient.invalidateQueries({ queryKey: RUNTIME_ARTIFACTS_QUERY_KEY });
      }
    },
  });

  const sessionLookup = useMemo(
    () => new Map((sessionsQuery.data ?? []).map((session) => [session.id, session])),
    [sessionsQuery.data],
  );

  async function handleStartRuntime(): Promise<void> {
    stopRuntimeMutation.reset();
    await startRuntimeMutation.mutateAsync();
  }

  async function handleStopRuntime(): Promise<void> {
    startRuntimeMutation.reset();
    await stopRuntimeMutation.mutateAsync();
  }

  async function handleExecuteSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    const trimmedCommand = command.trim();
    if (!trimmedCommand) {
      return;
    }

    const parsedTimeout = timeoutSeconds.trim().length > 0 ? Number(timeoutSeconds) : null;

    executeRuntimeMutation.reset();
    await executeRuntimeMutation.mutateAsync({
      command: trimmedCommand,
      timeout_seconds:
        typeof parsedTimeout === "number" && !Number.isNaN(parsedTimeout) ? parsedTimeout : null,
      session_id: sessionId.trim().length > 0 ? sessionId.trim() : null,
      artifact_paths: parseArtifactPaths(artifactPaths),
    });
  }

  async function handleUploadSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    if (!uploadFile || uploadPath.trim().length === 0) {
      return;
    }

    cleanupArtifactsMutation.reset();
    await uploadRuntimeMutation.mutateAsync({
      file: uploadFile,
      path: uploadPath.trim(),
      session_id: uploadSessionId.trim().length > 0 ? uploadSessionId.trim() : null,
      overwrite: uploadOverwrite,
    });
  }

  async function handleDownloadArtifact(artifact: RuntimeArtifact): Promise<void> {
    setDownloadingPath(artifact.relative_path);

    try {
      const blob = await downloadRuntimeArtifact(artifact.relative_path);
      triggerArtifactDownload(blob, getDownloadFileName(artifact.relative_path));
    } finally {
      setDownloadingPath(null);
    }
  }

  async function handleCleanupArtifacts(): Promise<void> {
    uploadRuntimeMutation.reset();
    await cleanupArtifactsMutation.mutateAsync();
  }

  const lifecycleError = startRuntimeMutation.isError
    ? startRuntimeMutation.error.message
    : stopRuntimeMutation.isError
      ? stopRuntimeMutation.error.message
      : null;

  if (runtimeStatusQuery.isLoading && !runtimeStatusQuery.data) {
    return (
      <main className="management-workbench management-workbench-single">
        <section className="management-unified-panel panel" aria-label="Runtime 工作台">
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">正在准备 Runtime</p>
            <p className="management-empty-copy">容器状态、最近执行和工件列表马上可用。</p>
          </div>
        </section>
      </main>
    );
  }

  if (runtimeStatusQuery.isError) {
    return (
      <main className="management-workbench management-workbench-single">
        <section className="management-unified-panel panel" aria-label="Runtime 工作台">
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">当前无法读取 Runtime</p>
            <p className="management-empty-copy">{runtimeStatusQuery.error.message}</p>
          </div>
        </section>
      </main>
    );
  }

  if (!runtimeStatusQuery.data) {
    return (
      <main className="management-workbench management-workbench-single">
        <section className="management-unified-panel panel" aria-label="Runtime 工作台">
          <div className="management-empty-state management-empty-state-full">
            <p className="management-empty-title">Runtime 数据为空</p>
            <p className="management-empty-copy">后端没有返回状态数据，请稍后重试。</p>
          </div>
        </section>
      </main>
    );
  }

  const runtime = runtimeStatusQuery.data.runtime;
  const runtimeHealth = runtimeHealthQuery.data ?? null;
  const runtimeProfiles = runtimeProfilesQuery.data ?? [];
  const recentRuns = runtimeRunsQuery.data ?? runtimeStatusQuery.data.recent_runs;
  const recentArtifacts = runtimeArtifactsQuery.data ?? runtimeStatusQuery.data.recent_artifacts;
  const isLifecyclePending = startRuntimeMutation.isPending || stopRuntimeMutation.isPending;
  const isExecuteDisabled = executeRuntimeMutation.isPending || command.trim().length === 0;
  const isUploadDisabled =
    uploadRuntimeMutation.isPending || !uploadFile || uploadPath.trim().length === 0;

  const requestedSessionId = sessionId.trim();
  const fallbackSessionId = recentRuns.find((run) => run.session_id)?.session_id ?? "";
  const policySessionId = requestedSessionId || fallbackSessionId;
  const activePolicySession = policySessionId ? (sessionLookup.get(policySessionId) ?? null) : null;

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="Runtime 工作台">
        <header className="management-unified-header">
          <div className="management-detail-copy">
            <span className="panel-kicker">Execution Plane</span>
            <h2 className="panel-title">Runtime</h2>
            <p className="management-unified-description">查看容器状态、执行命令并核对会话策略。</p>
          </div>

          <div className="management-action-row">
            <button
              className="button button-primary"
              type="button"
              disabled={isLifecyclePending || runtime.status === "running"}
              onClick={() => {
                void handleStartRuntime();
              }}
            >
              {startRuntimeMutation.isPending ? "启动中" : "启动 Runtime"}
            </button>
            <button
              className="button button-secondary"
              type="button"
              disabled={isLifecyclePending || runtime.status !== "running"}
              onClick={() => {
                void handleStopRuntime();
              }}
            >
              {stopRuntimeMutation.isPending ? "停止中" : "停止 Runtime"}
            </button>
          </div>
        </header>

        <div className="management-info-grid">
          <div className="management-info-card">
            <span className="management-info-label">运行状态</span>
            <div className="action-row">
              <StatusBadge status={runtime.status} />
              <span className="management-status-badge tone-neutral">{runtime.container_name}</span>
            </div>
          </div>
          <div className="management-info-card">
            <span className="management-info-label">健康检查</span>
            <strong
              className={`management-status-badge ${getHealthTone(runtimeHealth?.status ?? null)}`}
            >
              {runtimeHealth?.status ?? "未检测"}
            </strong>
          </div>
          <div className="management-info-card">
            <span className="management-info-label">镜像</span>
            <strong className="management-info-value management-info-code">{runtime.image}</strong>
          </div>
          <div className="management-info-card">
            <span className="management-info-label">容器 ID</span>
            <strong className="management-info-value management-info-code">
              {formatOptionalValue(runtime.container_id)}
            </strong>
          </div>
          <div className="management-info-card">
            <span className="management-info-label">启动时间</span>
            <strong className="management-info-value">
              {formatOptionalDateTime(runtime.started_at)}
            </strong>
          </div>
          <div className="management-info-card management-info-card-full">
            <span className="management-info-label">宿主机工作目录</span>
            <strong className="management-info-value management-info-code">
              {runtime.workspace_host_path}
            </strong>
          </div>
          <div className="management-info-card management-info-card-full">
            <span className="management-info-label">容器工作目录</span>
            <strong className="management-info-value management-info-code">
              {runtime.workspace_container_path}
            </strong>
          </div>
        </div>

        {lifecycleError ? <div className="management-error-banner">{lifecycleError}</div> : null}
        {runtimeHealthQuery.isError ? (
          <div className="management-error-banner">{runtimeHealthQuery.error.message}</div>
        ) : null}

        <SessionPolicySection
          activeSession={activePolicySession}
          isLoading={sessionsQuery.isLoading}
          requestedSessionId={policySessionId}
        />

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">运行能力</h3>
            <span className="management-status-badge tone-neutral">P1 / P2</span>
          </div>

          {runtimeProfilesQuery.isError ? (
            <div className="management-error-banner">{runtimeProfilesQuery.error.message}</div>
          ) : null}

          <div className="management-info-grid">
            <div className="management-info-card">
              <span className="management-info-label">健康状态</span>
              <strong
                className={`management-status-badge ${getHealthTone(runtimeHealth?.status ?? null)}`}
              >
                {runtimeHealth?.status ?? "未检测"}
              </strong>
            </div>
            <div className="management-info-card">
              <span className="management-info-label">Runtime 状态</span>
              <strong className="management-info-value">
                {runtimeHealth?.runtime_status ?? runtime.status}
              </strong>
            </div>
            <div className="management-info-card">
              <span className="management-info-label">可用 Profile</span>
              <strong className="management-info-value">{runtimeProfiles.length}</strong>
            </div>
            <div className="management-info-card management-info-card-full">
              <span className="management-info-label">健康检查工作目录</span>
              <strong className="management-info-value management-info-code">
                {runtimeHealth?.workspace_container_path ?? runtime.workspace_container_path}
              </strong>
            </div>
          </div>

          {runtimeProfilesQuery.isLoading && runtimeProfiles.length === 0 ? (
            <div className="management-inline-notice">正在读取 Runtime Profile。</div>
          ) : runtimeProfiles.length === 0 ? (
            <div className="management-inline-notice">
              当前未返回 Profile，后端会回退到默认策略。
            </div>
          ) : (
            <div className="management-list-shell">
              <ul className="management-list">
                {runtimeProfiles.map((profile) => (
                  <li key={profile.name} className="management-subcard">
                    <div className="management-list-card-header">
                      <strong className="management-list-title">{profile.name}</strong>
                      <span className="management-status-badge tone-neutral">profile</span>
                    </div>

                    <div className="management-info-grid">
                      <div className="management-info-card">
                        <span className="management-info-label">允许网络</span>
                        <strong
                          className={`management-status-badge ${profile.policy.allow_network ? "tone-success" : "tone-warning"}`}
                        >
                          {profile.policy.allow_network ? "允许" : "限制"}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">允许写入</span>
                        <strong
                          className={`management-status-badge ${profile.policy.allow_write ? "tone-success" : "tone-warning"}`}
                        >
                          {profile.policy.allow_write ? "允许" : "限制"}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">最长执行</span>
                        <strong className="management-info-value">
                          {profile.policy.max_execution_seconds}s
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">命令长度</span>
                        <strong className="management-info-value">
                          {profile.policy.max_command_length}
                        </strong>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">执行命令</h3>
          </div>

          <form className="settings-form" onSubmit={(event) => void handleExecuteSubmit(event)}>
            <label className="field-label" htmlFor="runtime-command">
              命令
              <textarea
                id="runtime-command"
                className="field-textarea"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
                placeholder="例如：printf 'analysis complete' > reports/result.txt"
              />
            </label>

            <div className="field-inline-group">
              <label className="field-label" htmlFor="runtime-timeout">
                超时秒数（可选）
                <input
                  id="runtime-timeout"
                  className="field-inline-input"
                  type="number"
                  min="1"
                  inputMode="numeric"
                  value={timeoutSeconds}
                  onChange={(event) => setTimeoutSeconds(event.target.value)}
                  placeholder="30"
                />
              </label>

              <label className="field-label" htmlFor="runtime-session-id">
                对话 ID（可选）
                <input
                  id="runtime-session-id"
                  className="field-inline-input"
                  type="text"
                  value={sessionId}
                  onChange={(event) => setSessionId(event.target.value)}
                  placeholder="填写后会绑定该对话和策略"
                />
              </label>

              <div className="field-label">
                工件登记
                <span className="management-unified-description">
                  每行一个相对路径，执行完成后后端会保留宿主机与容器内路径映射。
                </span>
              </div>
            </div>

            <label className="field-label" htmlFor="runtime-artifacts">
              工件路径（可选，每行一个）
              <textarea
                id="runtime-artifacts"
                className="field-textarea"
                value={artifactPaths}
                onChange={(event) => setArtifactPaths(event.target.value)}
                placeholder={"reports/result.txt\nlogs/runtime.log"}
              />
            </label>

            {executeRuntimeMutation.isError ? (
              <div className="management-error-banner">{executeRuntimeMutation.error.message}</div>
            ) : null}

            <div className="management-action-row">
              <button className="button button-primary" type="submit" disabled={isExecuteDisabled}>
                {executeRuntimeMutation.isPending ? "执行中" : "执行命令"}
              </button>
            </div>
          </form>
        </section>

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">工件维护</h3>
            <span className="management-status-badge tone-neutral">上传 / 清理</span>
          </div>

          <div className="management-dual-column">
            <form
              className="management-subcard settings-form"
              onSubmit={(event) => void handleUploadSubmit(event)}
            >
              <div className="management-section-header">
                <h4 className="management-section-title">上传到 Runtime</h4>
              </div>

              <label className="field-label" htmlFor="runtime-upload-file">
                文件
                <input
                  key={uploadInputResetKey}
                  id="runtime-upload-file"
                  className="field-input"
                  type="file"
                  onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                />
              </label>

              <label className="field-label" htmlFor="runtime-upload-path">
                目标路径
                <input
                  id="runtime-upload-path"
                  className="field-input"
                  type="text"
                  value={uploadPath}
                  onChange={(event) => setUploadPath(event.target.value)}
                  placeholder="例如：uploads/report.txt"
                />
              </label>

              <label className="field-label" htmlFor="runtime-upload-session-id">
                对话 ID（可选）
                <input
                  id="runtime-upload-session-id"
                  className="field-input"
                  type="text"
                  value={uploadSessionId}
                  onChange={(event) => setUploadSessionId(event.target.value)}
                  placeholder="绑定后会把上传动作登记到该对话"
                />
              </label>

              <label className="settings-inline-toggle">
                <input
                  type="checkbox"
                  checked={uploadOverwrite}
                  onChange={(event) => setUploadOverwrite(event.target.checked)}
                />
                已存在时覆盖目标文件
              </label>

              {uploadRuntimeMutation.isError ? (
                <div className="management-error-banner">{uploadRuntimeMutation.error.message}</div>
              ) : null}
              {uploadRuntimeMutation.isSuccess ? (
                <div className="management-inline-notice">上传已登记为一次 Runtime 执行记录。</div>
              ) : null}

              <div className="management-action-row">
                <button className="button button-primary" type="submit" disabled={isUploadDisabled}>
                  {uploadRuntimeMutation.isPending ? "上传中" : "上传工件"}
                </button>
              </div>
            </form>

            <div className="management-subcard">
              <div className="management-section-header">
                <h4 className="management-section-title">保留策略清理</h4>
              </div>

              <p className="management-unified-description">
                后端会按保留最近记录与保留期限自动判断可删除工件。这里显示的是清理动作结果，而不是策略配置本身。
              </p>

              <div className="management-info-grid">
                <div className="management-info-card">
                  <span className="management-info-label">当前工件数</span>
                  <strong className="management-info-value">{recentArtifacts.length}</strong>
                </div>
                <div className="management-info-card">
                  <span className="management-info-label">最近执行数</span>
                  <strong className="management-info-value">{recentRuns.length}</strong>
                </div>
              </div>

              {cleanupArtifactsMutation.isError ? (
                <div className="management-error-banner">
                  {cleanupArtifactsMutation.error.message}
                </div>
              ) : null}
              {cleanupArtifactsMutation.data ? (
                <div className="management-inline-notice">
                  已删除 {cleanupArtifactsMutation.data.deleted_files} 个文件、
                  {cleanupArtifactsMutation.data.deleted_rows} 条记录，保留{" "}
                  {cleanupArtifactsMutation.data.kept} 项。
                </div>
              ) : null}

              <div className="management-action-row">
                <button
                  className="button button-secondary"
                  type="button"
                  disabled={cleanupArtifactsMutation.isPending}
                  onClick={() => {
                    void handleCleanupArtifacts();
                  }}
                >
                  {cleanupArtifactsMutation.isPending ? "清理中" : "执行工件清理"}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">最近执行</h3>
            <span className="management-status-badge tone-neutral">{recentRuns.length} 项</span>
          </div>

          {runtimeRunsQuery.isError ? (
            <div className="management-error-banner">{runtimeRunsQuery.error.message}</div>
          ) : null}

          {recentRuns.length === 0 ? (
            <div className="management-empty-state">
              <p className="management-empty-title">还没有执行记录</p>
              <p className="management-empty-copy">
                提交一次命令后，这里会显示 stdout、stderr、退出码与工件。
              </p>
            </div>
          ) : (
            <ul className="management-list">
              {recentRuns.map((run) => {
                const attachedSession = run.session_id
                  ? (sessionLookup.get(run.session_id) ?? null)
                  : null;

                return (
                  <li
                    key={run.id}
                    className="management-section-card management-section-card-compact"
                  >
                    <div className="management-list-card-header">
                      <strong className="management-list-title">
                        {formatDateTime(run.started_at)}
                      </strong>
                      <StatusBadge status={run.status} />
                    </div>

                    <div className="action-row">
                      <span className="management-status-badge tone-neutral">
                        退出码 {run.exit_code ?? "n/a"}
                      </span>
                      <span className="management-status-badge tone-neutral">
                        超时 {run.requested_timeout_seconds}s
                      </span>
                      <span className="management-status-badge tone-neutral">
                        对话 {run.session_id ?? "未绑定"}
                      </span>
                    </div>

                    <div className="management-info-grid">
                      <div className="management-info-card management-info-card-full">
                        <span className="management-info-label">命令</span>
                        <pre className="management-code-block">{run.command}</pre>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">开始时间</span>
                        <strong className="management-info-value">
                          {formatDateTime(run.started_at)}
                        </strong>
                      </div>
                      <div className="management-info-card">
                        <span className="management-info-label">结束时间</span>
                        <strong className="management-info-value">
                          {formatDateTime(run.ended_at)}
                        </strong>
                      </div>
                      <div className="management-info-card management-info-card-full">
                        <span className="management-info-label">容器</span>
                        <strong className="management-info-value management-info-code">
                          {run.container_name}
                        </strong>
                      </div>
                      {attachedSession ? (
                        <div className="management-info-card management-info-card-full">
                          <span className="management-info-label">绑定对话</span>
                          <strong className="management-info-value">
                            {attachedSession.title}
                            {attachedSession.runtime_policy_json ? " · 已附带策略" : " · 无策略"}
                          </strong>
                        </div>
                      ) : null}
                    </div>

                    <div className="management-dual-column">
                      <div className="management-subcard">
                        <span className="management-info-label">stdout</span>
                        <pre className="management-code-block">{run.stdout || "(empty)"}</pre>
                      </div>
                      <div className="management-subcard">
                        <span className="management-info-label">stderr</span>
                        <pre className="management-code-block">{run.stderr || "(empty)"}</pre>
                      </div>
                    </div>

                    <div className="management-subcard">
                      <div className="management-section-header">
                        <h4 className="management-section-title">工件</h4>
                        <span className="management-status-badge tone-neutral">
                          {run.artifacts.length} 项
                        </span>
                      </div>
                      <RuntimeArtifactList
                        artifacts={run.artifacts}
                        downloadingPath={downloadingPath}
                        onDownload={(artifact) => {
                          void handleDownloadArtifact(artifact);
                        }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">工件登记表</h3>
            <span className="management-status-badge tone-neutral">
              {recentArtifacts.length} 项
            </span>
          </div>

          {runtimeArtifactsQuery.isError ? (
            <div className="management-error-banner">{runtimeArtifactsQuery.error.message}</div>
          ) : null}

          <RuntimeArtifactList
            artifacts={recentArtifacts}
            downloadingPath={downloadingPath}
            onDownload={(artifact) => {
              void handleDownloadArtifact(artifact);
            }}
          />
        </section>
      </section>
    </main>
  );
}
