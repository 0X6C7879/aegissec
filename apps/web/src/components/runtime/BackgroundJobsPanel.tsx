import type { TerminalJob, TerminalSession } from "../../types/terminals";

type BackgroundJobsPanelProps = {
  jobs: TerminalJob[];
  terminals: TerminalSession[];
  disabled?: boolean;
  onStopJob: (jobId: string) => void;
  onCleanup: () => void;
};

function findTerminalTitle(terminals: TerminalSession[], terminalId: string | null): string {
  if (!terminalId) {
    return "临时终端";
  }
  return terminals.find((terminal) => terminal.id === terminalId)?.title ?? terminalId;
}

export function BackgroundJobsPanel({
  jobs,
  terminals,
  disabled = false,
  onStopJob,
  onCleanup,
}: BackgroundJobsPanelProps) {
  return (
    <section className="shell-jobs-panel" data-testid="shell-jobs-panel">
      <header className="shell-jobs-header">
        <div>
          <h3 className="shell-section-title">后台任务</h3>
          <p className="shell-section-copy">展示 detach=true 的作业状态和停止入口。</p>
        </div>
        <button
          type="button"
          className="button button-secondary"
          onClick={onCleanup}
          disabled={disabled}
        >
          清理完成项
        </button>
      </header>

      {jobs.length === 0 ? (
        <div className="shell-empty-state">当前没有后台任务。</div>
      ) : (
        <div className="shell-jobs-list">
          {jobs.map((job) => {
            const isRunning = job.status === "running" || job.status === "queued";
            return (
              <article key={job.id} className="shell-job-card" data-testid={`shell-job-${job.id}`}>
                <div className="shell-job-row shell-job-row-primary">
                  <strong className="shell-job-command">{job.command}</strong>
                  <span className="shell-job-status">{job.status}</span>
                </div>
                <div className="shell-job-row shell-job-row-secondary">
                  <span>{findTerminalTitle(terminals, job.terminal_session_id ?? null)}</span>
                  <span>{job.id}</span>
                </div>
                <div className="shell-job-row shell-job-row-actions">
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={disabled || !isRunning}
                    onClick={() => onStopJob(job.id)}
                  >
                    停止
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
