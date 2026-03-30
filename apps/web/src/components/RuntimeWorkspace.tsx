import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  executeRuntimeCommand,
  getRuntimeStatus,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import type {
  RuntimeArtifact,
  RuntimeExecuteRequest,
  RuntimeExecutionRun,
  RuntimeState,
  RuntimeStatusResponse,
} from "../types/runtime";
import { StatusBadge } from "./StatusBadge";
import { WorkspaceNavigation } from "./WorkspaceNavigation";

const RUNTIME_STATUS_QUERY_KEY = ["runtime-status"] as const;

function formatOptionalDateTime(value: string | null): string {
  return value ? formatDateTime(value) : "Not started";
}

function formatOptionalValue(value: string | null): string {
  return value && value.trim().length > 0 ? value : "Not available";
}

function parseArtifactPaths(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((path) => path.trim())
    .filter((path) => path.length > 0);
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

function RuntimeArtifactList({ artifacts }: { artifacts: RuntimeArtifact[] }) {
  if (artifacts.length === 0) {
    return <p className="message-empty">No artifacts were registered for this execution.</p>;
  }

  return (
    <ul className="runtime-artifact-list">
      {artifacts.map((artifact) => (
        <li key={artifact.id} className="runtime-artifact-card">
          <div className="event-item-header">
            <strong className="runtime-artifact-title">{artifact.relative_path}</strong>
            <span className="timestamp-label">{formatDateTime(artifact.created_at)}</span>
          </div>
          <div className="runtime-detail-list">
            <div className="runtime-detail-item">
              <span className="runtime-detail-label">Host path</span>
              <code className="runtime-detail-value runtime-code-value">{artifact.host_path}</code>
            </div>
            <div className="runtime-detail-item">
              <span className="runtime-detail-label">Container path</span>
              <code className="runtime-detail-value runtime-code-value">{artifact.container_path}</code>
            </div>
            <div className="runtime-detail-item">
              <span className="runtime-detail-label">Run</span>
              <span className="runtime-detail-value">{artifact.run_id}</span>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function RuntimeWorkspace() {
  const queryClient = useQueryClient();
  const [command, setCommand] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [artifactPaths, setArtifactPaths] = useState("");

  const runtimeStatusQuery = useQuery({
    queryKey: RUNTIME_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => getRuntimeStatus(signal),
  });

  const startRuntimeMutation = useMutation({
    mutationFn: () => startRuntime(),
    onSuccess: async (runtime) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(RUNTIME_STATUS_QUERY_KEY, (currentValue) =>
        updateRuntimeState(currentValue, runtime),
      );
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
    },
  });

  const stopRuntimeMutation = useMutation({
    mutationFn: () => stopRuntime(),
    onSuccess: async (runtime) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(RUNTIME_STATUS_QUERY_KEY, (currentValue) =>
        updateRuntimeState(currentValue, runtime),
      );
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
    },
  });

  const executeRuntimeMutation = useMutation({
    mutationFn: (payload: RuntimeExecuteRequest) => executeRuntimeCommand(payload),
    onSuccess: async (run) => {
      queryClient.setQueryData<RuntimeStatusResponse | undefined>(RUNTIME_STATUS_QUERY_KEY, (currentValue) =>
        addRecentRun(currentValue, run),
      );
      setCommand("");
      setArtifactPaths("");
      await queryClient.invalidateQueries({ queryKey: RUNTIME_STATUS_QUERY_KEY });
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

  async function handleExecuteSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    const trimmedCommand = command.trim();
    if (!trimmedCommand) {
      return;
    }

    const parsedTimeout = timeoutSeconds.trim().length > 0 ? Number(timeoutSeconds) : undefined;

    executeRuntimeMutation.reset();
    await executeRuntimeMutation.mutateAsync({
      command: trimmedCommand,
      timeout_seconds: typeof parsedTimeout === "number" && !Number.isNaN(parsedTimeout) ? parsedTimeout : undefined,
      session_id: sessionId.trim().length > 0 ? sessionId.trim() : null,
      artifact_paths: parseArtifactPaths(artifactPaths),
    });
  }

  const lifecycleError = startRuntimeMutation.isError
    ? startRuntimeMutation.error.message
    : stopRuntimeMutation.isError
      ? stopRuntimeMutation.error.message
      : null;

  if (runtimeStatusQuery.isLoading) {
    return (
      <main className="workspace-shell">
        <WorkspaceNavigation />
        <section className="panel workspace-pane">
          <div className="empty-state">
            <p className="eyebrow">Controlled runtime</p>
            <h1 className="panel-title">Loading runtime status…</h1>
            <p className="empty-copy">
              Fetching container status, recent execution runs, and retained artifact metadata.
            </p>
          </div>
        </section>
      </main>
    );
  }

  if (runtimeStatusQuery.isError) {
    return (
      <main className="workspace-shell">
        <WorkspaceNavigation />
        <section className="panel workspace-pane">
          <div className="empty-state">
            <p className="eyebrow">Controlled runtime</p>
            <h1 className="panel-title">Runtime status unavailable</h1>
            <p className="empty-copy">{runtimeStatusQuery.error.message}</p>
          </div>
        </section>
      </main>
    );
  }

  if (!runtimeStatusQuery.data) {
    return (
      <main className="workspace-shell">
        <WorkspaceNavigation />
        <section className="panel workspace-pane">
          <div className="empty-state">
            <p className="eyebrow">Controlled runtime</p>
            <h1 className="panel-title">Runtime data unavailable</h1>
            <p className="empty-copy">
              The runtime API returned no data. Refresh the page and try again.
            </p>
          </div>
        </section>
      </main>
    );
  }

  const runtime = runtimeStatusQuery.data.runtime;
  const recentRuns = runtimeStatusQuery.data.recent_runs;
  const recentArtifacts = runtimeStatusQuery.data.recent_artifacts;
  const isLifecyclePending = startRuntimeMutation.isPending || stopRuntimeMutation.isPending;
  const isExecuteDisabled = executeRuntimeMutation.isPending || command.trim().length === 0;

  return (
    <main className="workspace-shell">
      <WorkspaceNavigation />

      <div className="workspace-layout">
        <div className="sidebar-column">
          <aside className="panel session-sidebar runtime-sidebar">
            <div className="panel-header">
              <div className="panel-header-copy">
                <p className="eyebrow">Controlled execution</p>
                <h1 className="panel-title">Runtime</h1>
                <p className="panel-description">
                  Monitor the managed container, start or stop it explicitly, and keep execution evidence
                  tied to authorized validation work.
                </p>
              </div>
            </div>

            <div className="action-row">
              <StatusBadge status={runtime.status} />
              <span className="meta-pill">{runtime.container_name}</span>
            </div>

            {lifecycleError ? <div className="notice runtime-notice-error">{lifecycleError}</div> : null}

            <div className="button-row">
              <button
                className="button button-primary"
                type="button"
                disabled={isLifecyclePending || runtime.status === "running"}
                onClick={() => {
                  void handleStartRuntime();
                }}
              >
                {startRuntimeMutation.isPending ? "Starting…" : "Start runtime"}
              </button>
              <button
                className="button button-secondary"
                type="button"
                disabled={isLifecyclePending || runtime.status !== "running"}
                onClick={() => {
                  void handleStopRuntime();
                }}
              >
                {stopRuntimeMutation.isPending ? "Stopping…" : "Stop runtime"}
              </button>
            </div>

            <div className="runtime-detail-list">
              <div className="runtime-detail-item">
                <span className="runtime-detail-label">Image</span>
                <code className="runtime-detail-value runtime-code-value">{runtime.image}</code>
              </div>
              <div className="runtime-detail-item">
                <span className="runtime-detail-label">Container ID</span>
                <span className="runtime-detail-value">{formatOptionalValue(runtime.container_id)}</span>
              </div>
              <div className="runtime-detail-item">
                <span className="runtime-detail-label">Started</span>
                <span className="runtime-detail-value">{formatOptionalDateTime(runtime.started_at)}</span>
              </div>
              <div className="runtime-detail-item">
                <span className="runtime-detail-label">Host workspace</span>
                <code className="runtime-detail-value runtime-code-value">{runtime.workspace_host_path}</code>
              </div>
              <div className="runtime-detail-item">
                <span className="runtime-detail-label">Container workspace</span>
                <code className="runtime-detail-value runtime-code-value">
                  {runtime.workspace_container_path}
                </code>
              </div>
            </div>
          </aside>
        </div>

        <div className="main-column">
          <section className="panel workspace-pane runtime-pane">
            <header className="workspace-pane-header">
              <div className="panel-header-copy">
                <p className="eyebrow">Execution history</p>
                <h2 className="session-title">Recent runs and artifacts</h2>
                <p className="session-copy">
                  Execute container commands, optionally link them to a session, and review stdout,
                  stderr, exit codes, and retained artifact paths in one place.
                </p>
              </div>
            </header>

            <div className="session-summary-grid runtime-summary-grid">
              <article className="summary-card">
                <p className="summary-label">Recent runs</p>
                <p className="summary-value">{recentRuns.length}</p>
                <p className="session-meta-copy">Most recent execution results returned by the runtime API.</p>
              </article>

              <article className="summary-card">
                <p className="summary-label">Recent artifacts</p>
                <p className="summary-value">{recentArtifacts.length}</p>
                <p className="session-meta-copy">Artifact registrations remain visible across the latest runs.</p>
              </article>

              <article className="summary-card">
                <p className="summary-label">Workspace mount</p>
                <p className="summary-value runtime-summary-path">{runtime.workspace_container_path}</p>
                <p className="session-meta-copy">Commands run inside the mounted workspace path shown above.</p>
              </article>
            </div>

            <section className="chat-compose-panel runtime-form-panel">
              <div className="panel-header">
                <div className="panel-header-copy">
                  <p className="eyebrow">Execute</p>
                  <h2 className="panel-title">Run a command</h2>
                </div>
              </div>

              <form className="compose-form" onSubmit={handleExecuteSubmit}>
                <label className="field-label" htmlFor="runtime-command">
                  Command
                  <textarea
                    id="runtime-command"
                    className="field-textarea"
                    value={command}
                    onChange={(event) => setCommand(event.target.value)}
                    placeholder="Example: printf 'analysis complete' > reports/result.txt"
                  />
                </label>

                <div className="field-inline-group">
                  <label className="field-label" htmlFor="runtime-timeout">
                    Timeout seconds (optional)
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
                    Session ID (optional)
                    <input
                      id="runtime-session-id"
                      className="field-inline-input"
                      type="text"
                      value={sessionId}
                      onChange={(event) => setSessionId(event.target.value)}
                      placeholder="Attach this run to a retained session"
                    />
                  </label>

                  <div className="field-label runtime-form-help">
                    Artifact capture
                    <span className="panel-description">
                      Register relative artifact paths so the backend can retain their mounted host and
                      container locations after execution.
                    </span>
                  </div>
                </div>

                <label className="field-label" htmlFor="runtime-artifacts">
                  Artifact paths (optional, one relative path per line)
                  <textarea
                    id="runtime-artifacts"
                    className="field-textarea runtime-artifact-input"
                    value={artifactPaths}
                    onChange={(event) => setArtifactPaths(event.target.value)}
                    placeholder={"reports/result.txt\nlogs/runtime.log"}
                  />
                </label>

                {executeRuntimeMutation.isError ? (
                  <div className="notice runtime-notice-error">{executeRuntimeMutation.error.message}</div>
                ) : null}

                <div className="button-row">
                  <button className="button button-primary" type="submit" disabled={isExecuteDisabled}>
                    {executeRuntimeMutation.isPending ? "Executing…" : "Execute command"}
                  </button>
                </div>
              </form>
            </section>

            <section className="message-timeline runtime-section">
              <div className="panel-header">
                <div className="panel-header-copy">
                  <p className="eyebrow">Recent runs</p>
                  <h2 className="panel-title">Execution results</h2>
                </div>
              </div>

              {recentRuns.length === 0 ? (
                <p className="message-empty">
                  No runtime executions have been recorded yet. Submit a command above to capture the
                  first run, including stdout, stderr, exit code, and any declared artifacts.
                </p>
              ) : (
                <div className="runtime-run-list">
                  {recentRuns.map((run) => (
                    <article key={run.id} className="message-item runtime-run-card">
                      <div className="runtime-run-header">
                        <div className="panel-header-copy runtime-run-copy">
                          <div className="action-row">
                            <StatusBadge status={run.status} />
                            <span className="meta-pill">Exit code {run.exit_code ?? "n/a"}</span>
                            <span className="meta-pill">Timeout {run.requested_timeout_seconds}s</span>
                          </div>
                          <p className="runtime-run-command-label">Command</p>
                          <pre className="runtime-command">{run.command}</pre>
                        </div>

                        <div className="runtime-run-meta">
                          <span className="session-meta-copy">Started {formatDateTime(run.started_at)}</span>
                          <span className="session-meta-copy">Ended {formatDateTime(run.ended_at)}</span>
                          <span className="session-meta-copy">
                            Session {run.session_id ?? "Not attached"}
                          </span>
                          <span className="session-meta-copy">Container {run.container_name}</span>
                        </div>
                      </div>

                      <div className="runtime-output-grid">
                        <section className="runtime-output-panel">
                          <div className="event-item-header">
                            <strong className="runtime-output-title">stdout</strong>
                          </div>
                          <pre className="runtime-output">{run.stdout || "(empty)"}</pre>
                        </section>

                        <section className="runtime-output-panel">
                          <div className="event-item-header">
                            <strong className="runtime-output-title">stderr</strong>
                          </div>
                          <pre className="runtime-output">{run.stderr || "(empty)"}</pre>
                        </section>
                      </div>

                      <section className="runtime-run-artifacts">
                        <div className="panel-header">
                          <div className="panel-header-copy">
                            <p className="eyebrow">Artifacts</p>
                            <h3 className="panel-title runtime-subsection-title">Run outputs</h3>
                          </div>
                        </div>
                        <RuntimeArtifactList artifacts={run.artifacts} />
                      </section>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className="message-timeline runtime-section">
              <div className="panel-header">
                <div className="panel-header-copy">
                  <p className="eyebrow">Recent artifacts</p>
                  <h2 className="panel-title">Artifact registry</h2>
                </div>
              </div>

              {recentArtifacts.length === 0 ? (
                <p className="message-empty">
                  No artifacts have been registered yet. Add relative artifact paths when executing a
                  command to keep the resulting files visible here.
                </p>
              ) : (
                <RuntimeArtifactList artifacts={recentArtifacts} />
              )}
            </section>
          </section>
        </div>
      </div>
    </main>
  );
}
