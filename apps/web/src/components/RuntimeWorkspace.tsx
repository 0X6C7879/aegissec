import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearRuntimeRuns,
  executeRuntimeCommand,
  getRuntimeHealth,
  getRuntimeStatus,
  listRuntimeRuns,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { RuntimeExecutionRun, RuntimeState, RuntimeStatusResponse } from "../types/runtime";
import { StatusBadge } from "./StatusBadge";

const RUNTIME_STATUS_QUERY_KEY = ["runtime-status"] as const;
const RUNTIME_HEALTH_QUERY_KEY = ["runtime-health"] as const;
const RUNTIME_RUNS_QUERY_KEY = ["runtime-runs"] as const;

function formatOptionalDateTime(value: string | null): string {
  return value ? formatDateTime(value) : "未启动";
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
    recent_runs: [run, ...currentValue.recent_runs.filter((currentRun) => currentRun.id !== run.id)],
    recent_artifacts: [
      ...run.artifacts,
      ...currentValue.recent_artifacts.filter(
        (artifact) => !run.artifacts.some((runArtifact) => runArtifact.id === artifact.id),
      ),
    ],
  };
}

function clearRecentRunsCache(
  currentValue: RuntimeStatusResponse | undefined,
): RuntimeStatusResponse | undefined {
  if (!currentValue) {
    return currentValue;
  }

  return {
    ...currentValue,
    recent_runs: [],
    recent_artifacts: [],
  };
}

export function RuntimeWorkspace() {
  const queryClient = useQueryClient();
  const [command, setCommand] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("");

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
    mutationFn: (payload: { command: string; timeout_seconds?: number | null }) =>
      executeRuntimeCommand({
        ...payload,
        artifact_paths: [],
        session_id: null,
      }),
    onSuccess: async (run) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(
        RUNTIME_STATUS_QUERY_KEY,
        (currentValue) => addRecentRun(currentValue, run),
      );
      setCommand("");
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_RUNS_QUERY_KEY });
    },
  });

  const clearRuntimeRunsMutation = useMutation({
    mutationFn: () => clearRuntimeRuns(),
    onSuccess: async () => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(
        RUNTIME_STATUS_QUERY_KEY,
        (currentValue) => clearRecentRunsCache(currentValue),
      );
      queryClient.setQueryData<RuntimeExecutionRun[] | undefined>(RUNTIME_RUNS_QUERY_KEY, []);
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: RUNTIME_RUNS_QUERY_KEY });
    },
  });

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

    await executeRuntimeMutation.mutateAsync({
      command: trimmedCommand,
      timeout_seconds:
        typeof parsedTimeout === "number" && !Number.isNaN(parsedTimeout) ? parsedTimeout : null,
    });
  }

  async function handleClearRecentRuns(): Promise<void> {
    if (clearRuntimeRunsMutation.isPending) {
      return;
    }

    await clearRuntimeRunsMutation.mutateAsync();
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
            <p className="management-empty-copy">容器状态与最近执行记录马上可用。</p>
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
  const recentRuns = runtimeRunsQuery.data ?? runtimeStatusQuery.data.recent_runs;
  const isLifecyclePending = startRuntimeMutation.isPending || stopRuntimeMutation.isPending;
  const isExecuteDisabled =
    runtime.status !== "running" || executeRuntimeMutation.isPending || command.trim().length === 0;
  const mutationErrorMessage = executeRuntimeMutation.isError
    ? executeRuntimeMutation.error.message
    : clearRuntimeRunsMutation.isError
      ? clearRuntimeRunsMutation.error.message
      : null;

  return (
    <main className="management-workbench management-workbench-single">
      <section className="management-unified-panel panel" aria-label="Runtime 工作台">
        <header className="management-unified-header">
          <div className="management-detail-copy">
            <span className="panel-kicker">Execution Plane</span>
            <h2 className="panel-title">Runtime</h2>
            <p className="management-unified-description">仅保留核心控制：启动、停止、执行命令和查看最近执行。</p>
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
            <StatusBadge status={runtime.status} />
          </div>
          <div className="management-info-card">
            <span className="management-info-label">健康检查</span>
            <strong className={`management-status-badge ${runtimeHealth?.status === "ok" ? "tone-success" : runtimeHealth?.status === "degraded" ? "tone-warning" : "tone-neutral"}`}>
              {runtimeHealth?.status ?? "未检测"}
            </strong>
          </div>
          <div className="management-info-card">
            <span className="management-info-label">启动时间</span>
            <strong className="management-info-value">{formatOptionalDateTime(runtime.started_at)}</strong>
          </div>
        </div>

        {lifecycleError ? <div className="management-error-banner">{lifecycleError}</div> : null}
        {runtimeHealthQuery.isError ? (
          <div className="management-error-banner">{runtimeHealthQuery.error.message}</div>
        ) : null}
        {mutationErrorMessage ? <div className="management-error-banner">{mutationErrorMessage}</div> : null}
        {clearRuntimeRunsMutation.isSuccess ? (
          <div className="management-inline-notice">
            已清除 {clearRuntimeRunsMutation.data.deleted_runs} 条执行记录，连带移除 {clearRuntimeRunsMutation.data.deleted_artifacts} 条工件登记。
          </div>
        ) : null}

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">执行命令</h3>
            <span className="management-status-badge tone-neutral">
              {runtime.status === "running" ? "可执行" : "请先启动 Runtime"}
            </span>
          </div>

          <form className="settings-form" onSubmit={(event) => void handleExecuteSubmit(event)}>
            <label className="field-label" htmlFor="runtime-command">
              命令
              <textarea
                id="runtime-command"
                className="field-textarea"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
                placeholder="例如：python --version"
              />
            </label>

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

            <div className="management-action-row">
              <button className="button button-primary" type="submit" disabled={isExecuteDisabled}>
                {executeRuntimeMutation.isPending ? "执行中" : "执行命令"}
              </button>
            </div>
          </form>
        </section>

        <section className="management-section-card">
          <div className="management-section-header">
            <h3 className="management-section-title">最近执行</h3>
            <div className="management-action-row">
              <span className="management-status-badge tone-neutral">{recentRuns.length} 项</span>
              <button
                className="button button-secondary"
                type="button"
                disabled={recentRuns.length === 0 || clearRuntimeRunsMutation.isPending}
                onClick={() => {
                  void handleClearRecentRuns();
                }}
              >
                {clearRuntimeRunsMutation.isPending ? "清除中" : "清除"}
              </button>
            </div>
          </div>

          {runtimeRunsQuery.isError ? (
            <div className="management-error-banner">{runtimeRunsQuery.error.message}</div>
          ) : null}

          {recentRuns.length === 0 ? (
            <div className="management-empty-state">
              <p className="management-empty-title">还没有执行记录</p>
              <p className="management-empty-copy">提交一次命令后，这里会保留最近的执行结果。</p>
            </div>
          ) : (
            <ul className="management-list">
              {recentRuns.map((run) => (
                <li key={run.id} className="management-section-card management-section-card-compact">
                  <div className="management-list-card-header">
                    <strong className="management-list-title">{formatDateTime(run.started_at)}</strong>
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
                      结束 {formatDateTime(run.ended_at)}
                    </span>
                  </div>

                  <div className="management-info-card management-info-card-full">
                    <span className="management-info-label">命令</span>
                    <pre className="management-code-block">{run.command}</pre>
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
                </li>
              ))}
            </ul>
          )}
        </section>
      </section>
    </main>
  );
}
